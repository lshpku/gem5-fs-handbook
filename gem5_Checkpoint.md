# gem5 Checkpoint原理与代码
本文档基于gem5 v22.0.0.1版本

gem5虽然提供了`se.py`脚本来“方便”地操作Checkpoint，但`se.py`包装得太好，导致用户不能很好地理解其中的原理，而且也很难修改它以实现自己的需求。为了从原理上学会操作gem5的Checkpoint，我写了这份文档。本文档的配套代码是[checkpoint.py](checkpoint.py)，这些代码基本上是仿照`se.py`实现的，确保了正确性，但没有做过多的包装，所以很好理解和修改。

## 创建Checkpoint
* 创建Checkpoint很简单，只要调用在`m5.simulate()`返回后调用`m5.checkpoint(cpt_path)`即可，后者的原理为
  * 调用`drain()`：经由`DrainManager`调用所有`Drainable`对象的`drain()`函数（`Drainable`包括所有`SimObject`），使其达到一个适于保存的状态
  * 调用`memWriteback(root)`：调用root下所有对象的`memWriteback()`函数，这个函数是每个`SimObject`都有的，但大部分都是空的，只有`BaseCache`实现了这个函数，在调用时将Cache中的脏块写回内存
  * 调用`serializeAll(dir)`：调用所有`SimObject`的`serializeSection()`和`serialize()`函数，将其作为一个键值对写入`<dir>/m5.cpt`文件中
    * <b>注：</b>`PhysicalMemory`比较特殊，它会用一个特殊的`serializeStore()`把内存内容压缩后写入`<dir>/system.physmem.store0.pmem`中，不和其他对象写在一起
* `m5.simulate()`默认是运行到程序结束（除非程序中调用了`m5`的`intrinsic`导致退出），这样就没有Checkpoint的意义了，所以我们需要让它在程序中途能够暂停，有两种方法
### 运行至tick数
* gem5使用`tick`作为时间单位进行计数，1`tick`默认是1ps，所以如果CPU主频是2GHz，每个cycle就等于500`tick`
* `m5.simulate()`有一个参数`ticks`（<b>注：</b>调用时通常不写参数名），指本次模拟在超过`ticks`时停止，因此实际结束时的`tick`数可能略大
* `m5.simulate()`可以重复调用，因此可以让它每次运行一个固定的`tick`数，每次运行完都创建一个Checkpoint
* 运行样例
  ```bash
  $ build/RISCV/gem5.opt checkpoint.py create_by_fixed_ticks
  ```
### 运行至指令数
* 通过`m5.simulate()`指定最大`tick`数是gem5的基础框架提供的功能，但是如果我们想用指令数作为上限就不行，因为指令数取决于CPU，而CPU有不同的类型（如`Atomic`、`O3`等），基础框架无从限制它们的指令数；想要通过指令数限制运行上限，就要直接给CPU指定
* gem5有两种方法指定CPU的最大指令数，一是在初始化（即调用`m5.instantiate()`）之前指定，如下所示；这个值直接传入CPU的构造函数，无法更改，故只能设定一次，不适用于反复暂停的情况
  ```python
  system.cpu.max_insts_any_thread = 10000
  ```
* 另一种更好方法是每次调用`m5.simulate()`前给CPU计划一个指令限制事件，CPU在执行到该指令数时就会触发事件返回，达到暂停的效果，如下所示
  ```python
  system.cpu.scheduleInstStop(0, 10000, 'inst stop')
  ```
* 运行完整样例，注意区分`m5.simulate()`的返回值
  ```bash
  $ build/RISCV/gem5.opt checkpoint.py create_by_fixed_insts
  ```

## 恢复Checkpoint
* 恢复Checkpoint也很简单，只要在初始化时指定Checkpoint的路径：`m5.instantiate(ckpt_dir)`，原理为
  * 调用`DrainManager`的`preCheckpointRestore()`：将所有`Drainable`对象的状态设为`Drained`
  * 调用`getCheckpoint(ckpt_dir)`：构造一个`CheckpointIn`对象，用这个对象来读Checkpoint中的内容
  * 对于`root`下的所有对象，调用其`loadState(ckpt)`，进而调用`unserializeSection()`和`unserialize()`，将之前存入的键值对读出来并恢复
* <b>警告：</b>Checkpoint并未记录`system`的结构，它只是把`system`中每个模块的内容记录了下来，所以在恢复Checkpoint之前必须保证`system`的结构已经配置成保存Checkpoint时的样子（包括连接、参数等），否则无法正确恢复
* 运行样例，以恢复`ckpt.001`为例，其他的同理
  ```bash
  $ build/RISCV/gem5.opt checkpoint.py restore
  ```
* 恢复Checkpoint后`stats`会被重新统计，因此`stats`记录的是恢复之后这段模拟过程的统计信息，例如查看CPU经过的周期数
  ```bash
  $ cat m5out/stats.txt | grep numCycles
  # system.cpu.numCycles    55965    # Number of cpu cycles simulated (Cycle)
  ```

## 切换CPU
* 很多情况下，我们希望恢复Checkpoint时使用和保存Checkpoint时不同的配置（例如保存时用`Atomic`，恢复时用`O3`），这无法直接通过恢复Checkpoint实现，我们需要用gem5的另一个接口函数：`m5.switchCpus()`
* `m5.switchCpus()`的使用方法比较麻烦，需要将交换的CPU对作为参数传入，如下所示
  ```python
  switch_cpu_list = [(old_cpu, new_cpu)]
  m5.switchCpus(system, switch_cpu_list)
  ```
* `m5.switchCpus()`的原理为
  * 调用`drain()`，作用同`m5.checkpoint()`
  * 调用`old_cpu.switchOut()`：`old_cpu`调用`flushTLBs()`，设置`PowerState`为`OFF`
  * 如果`system`的内存模式需要改变（例如把CPU从`Atomic`改成`O3`时需要把内存模式从`atomic`改成`timing`），则
    * 先用`memWriteback(system)`和`memInvalidate(system)`写回并清空Cache
    * 然后用`system.setMemoryMode()`改变内存模式
  * 调用`new_cpu.takeOverFrom(old_cpu)`：`new_cpu`将继承`old_cpu`的`ThreadContext`，即`PC`和体系结构寄存器（由各ISA自己实现）
    * <b>注：</b>`takeOverFrom()`这个接口来自`BaseCPU`，gem5的CPU都实现了这一接口，所以可以互相切换
* <b>注：</b>切换CPU和创建/恢复Checkpoint没有必然关系，恢复Checkpoint时不一定要切换CPU，在不创建Checkpoint的情况下也可以切换CPU
* 恢复Checkpoint时切换CPU的样例（我模仿了`Simulation.py`，在切换前先执行10000`tick`，不清楚是否有必要）
  ```bash
  $ build/RISCV/gem5.opt checkpoint.py restore_and_switch
  ```
* 不创建Checkpoint，只是不停地来回切换的样例
  ```bash
  $ build/RISCV/gem5.opt checkpoint.py switch_repeatedly
  ```
* 在切换CPU的情况下，`system`中会同时存在两个CPU，故`stats`中也有两个CPU的统计信息，在读取时务必注意区分（通常我们只需要`switch_cpu`）
  ```bash
  $ cat m5out/stats.txt | grep numCycles
  # system.cpu.numCycles              20    # Number of cpu cycles simulated (Cycle)
  # system.switch_cpu.numCycles    39494    # Number of cpu cycles simulated (Cycle)
  ```

## 创建SimPoint Checkpoint
* SimPoint Checkpoint不是一种全新的Chekpoint，它只是在普通Checkpoint的基础上增加了对BBV的分析，所以它的创建方法也是在创建普通Checkpoint方法上的扩展
* <b>注：</b>只有`AtomicSimpleCPU`支持SimPoint，显然`Atomic`是最适合用来创建SimPoint的CPU，所以gem5只在它上面提供了SimPoint接口
* 创建SimPoint Checkpoint的大致步骤为
  * 在初始化`system`前，调用CPU（必须是`Atomic`或其子类）的`addSimPointProbe()`函数，给CPU挂载一个`SimPoint`实例
    * `SimPoint`是`ProbeListenerObject`的子类，它可以监听CPU的`Commit`阶段，用提交的指令计算BBV
  * 首次运行程序，每执行intervalCount条指令，`SimPoint`就会输出一段BBV；首次运行程序的过程中只生成BBV，不生成Checkpoint
  * 得到整个程序的BBV后，用SimPoint 3.2工具（需另外安装）分析，得到Weight
  * 重新运行一次程序，在初始化`system`前，给CPU指定`simpoint_start_insts`，标记SimPoint的断点位置，在这些断点处生成SimPoint Checkpoint
* <b>注：</b>也就是说，生成SimPoint Checkpoint需要运行一个程序两次
  * 理论上也可以只运行一次，在生成BBV的同时以相同的`interval`创建Checkpoint；不过既然`se.py`用的是两次的方案，可能也是有它的考虑
  * 我觉得一个好处可能是第一次生成BBV的时候可以用`Atomic`，第二次生成Checkpoint的时候可以用`O3`，于是也可以保存Cache内容
  * <b>警告：</b>这两次必须设置相同的`interval`，否则两次的切片对不上
* 生成SimPoint BBV样例
  ```bash
  $ build/RISCV/gem5.opt checkpoint.py simpoint_profile
  ```
* 要生成Weight，请先自行安装[SimPoint 3.2](https://cseweb.ucsd.edu/~calder/simpoint/simpoint-3-0.htm)，然后运行以下命令（`-maxK 5`意思是最多生成5个SimPoint）
  ```bash
  $ /path/to/simpoint -maxK 5 \
    -loadFVFile m5out/simpoint.bb.gz -inputVectorsGzipped \
    -saveSimpoints m5out/simpoints.txt \
    -saveSimpointWeights m5out/weights.txt
  ```
* 生成SimPoint Checkpoint样例，我按从`cpkt.001`开始递增地给Checkpoint命名，你也可以改成自己的命名方式
  ```bash
  $ build/RISCV/gem5.opt checkpoint.py take_simpoint_checkpoints
  ```
* 恢复SimPoint Checkpoint样例，这里仅恢复`ckpt.001`，其他的同理；和一般的恢复不同的是，SimPoint只是运行一个区间，不会运行到结束，所以要设定一个指令数上限；我们可以继续用`simpoint_start_insts`置warmup指令数和总指令数（虽然名字不是这个意思）
  ```bash
  $ build/RISCV/gem5.opt checkpoint.py restore_simpoint
  ```
