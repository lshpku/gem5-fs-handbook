# gem5 FS模式实验手册

基于`v22.0.0.1`版本的gem5 Full System（FS）模式实验手册

由于gem5的主分支更新很快，但各ISA未必跟上主分支的更新，导致出现连教程都无法运行的情况，所以我写了这份基于`v22.0.0.1`版本的实验手册；这个版本解决了RISC-V的RVC指令的性能问题，非常适合做RISC-V的FS模式的实验。

## 编译安装gem5

### 环境准备
* 根据[Building gem5](https://www.gem5.org/documentation/general_docs/building)准备环境
* 自行安装RISC-V工具链，从源码编译或用`apt`安装均可

### 获取代码
* clone本仓库
  ```bash
  $ git clone https://github.com/lshpku/gem5-fs-handbook.git
  ```
* 获取gem5源代码，clone时不checkout以加快速度
  ```bash
  $ git clone --no-checkout https://github.com/gem5/gem5.git
  ```
* checkout正确的分支
  <b>警告：</b>本手册严格基于`v22.0.0.1`版本的gem5编写，使用其他版本出现问题概不负责！
  ```bash
  $ cd gem5
  $ git checkout v22.0.0.1
  ```

### 打patch
* `v22.0.0.1`版本的`HiFive.py`板子新增了PCI设备，但它的配套脚本（`fs_linux.py`）并不支持这一设备
* 开发者又提供了一个新的板子（`riscv_board.py`）和脚本（`riscv-fs.py`），但新的脚本非常不完善，连基本的命令行参数都没有
* 我提供了一个patch，可以移除`HiFive.py`中PCI的部分，这样就可以用原来的脚本了
* 使用如下命令应用patch
  ```bash
  $ cd gem5
  $ git apply ../gem5-fs-handbook/remove-pci.patch
  ```

### 编译
* 使用SCons编译，实测在24核的机器上用时10分钟
  ```bash
  $ python3 `which scons` build/RISCV/gem5.opt -j`nproc`
  ```

## SE模式

本节首先测试一下Syscall Emulation（SE）模式是否正常

### Hello World
* 编译gem5自带的Hello World
  <b>注：</b>SE模式务必使用`-static`编译
  ```bash
  $ cd gem5
  $ riscv64-unknown-elf-gcc tests/test-progs/hello/src/hello.c -static -o hello
  ```
* 使用`se.py`脚本运行，gem5输出较多，这里只截取部分
  ```bash
  $ build/RISCV/gem5.opt configs/example/se.py -c hello
  # gem5 Simulator System.  https://www.gem5.org
  # ...
  # **** REAL SIMULATION ****
  # build/RISCV/sim/simulate.cc:194: info: Entering event queue @ 0.  Starting simulation...
  # Hello world!
  # Exiting @ tick 1056500 because exiting with last active thread context
  ```

### 输出动态指令流信息
* gem5自带的`DPRINTF`功能可以输出系统中各个组件（包括流水线、内存系统等）的动态信息，`DPRINTF`默认是关闭的，需使用`--debug-flags`开启
* 例如，下面开启程序的动态PC流输出，写到`m5out/debug.log.gz`文件中
  <b>注：</b>`--debug-file`的路径含有一个隐式前缀`m5out/`
  ```bash
  build/RISCV/gem5.opt \
    --debug-flags=ExecEnable,ExecUser,ExecKernel \
    --debug-file=debug.log.gz \
    configs/example/se.py \
    --cmd=hello
  ```
* 为了节约磁盘空间，我们使用压缩格式记录输出，可以使用如下命令查看前10行输出
  ```bash
  $ gzip -dc m5out/debug.log.gz | head -10
  #  500: system.cpu: 0x10116    : auipc gp, 4                :
  # 1500: system.cpu: 0x1011a    : addi gp, gp, -638          :
  # 2500: system.cpu: 0x1011e    : addi a0, gp, 1912          :
  # 3500: system.cpu: 0x10122    : auipc a2, 4                :
  # 4500: system.cpu: 0x10126    : addi a2, a2, 1398          :
  # ...
  ```
* 更多的`--debug-flag`可以用下面的命令查看
  ```bash
  $ build/RISCV/gem5.opt --debug-help
  # ...
  # Compound Flags:
  #     AnnotateAll: All Annotation flags
  #         Annotate, AnnotateQ, AnnotateVerbose
  #     CacheAll:
  #         Cache, CacheComp, CachePort, CacheRepl, CacheVerbose,
  #         HWPrefetch, MSHR
  #　...
  ```

### 输出性能计数器
* 若我们并不关心每条动态指令，只是想知道整个程序最终的运行情况，可以用gem5的性能计数器功能
* gem5的性能计数器默认是开启的，例如运行下面的命令
  ```bash
  build/RISCV/gem5.opt configs/example/se.py \
    --cpu-type=O3CPU \
    --bp-type=TAGE_SC_L_64KB \
    --caches --l2cache \
    --cmd=hello
  ```
* 就可以在`m5out/stats.txt`中看到整个程序最终的性能计数器的值
  ```bash
  $ cat m5out/stats.txt
  # ...
  # system.cpu.numCycles       13156  # Number of cpu cycles simulated (Cycle)
  # system.cpu.committedInsts   1760  # Number of Instructions Simulated (Count)
  # system.cpu.instsIssued      2665  # Number of instructions issued (Count)
  # ...
  ```

## FS模式：启动Linux

本节介绍如何使用FS模式启动一个最基本的操作系统

### 编译m5term
* `m5term`是gem5自带的用于连接控制台的小程序，直接编译即可
  ```bash
  $ cd gem5/util/term
  $ make CFLAGS=-O3
  ```

### 获取RISC-V内核与磁盘镜像
* 进入网页[RISCV Full System](http://resources.gem5.org/resources/riscv-fs)
* 下载Bootloader：[bootloader-vmlinux-5.10](http://dist.gem5.org/dist/v21-2/kernels/riscv/static/bootloader-vmlinux-5.10)
  * <b>注：</b>不是下载Kernel，只有Kernel无法启动，必须下载内嵌了Kernel的Bootloader
* 下载Disk Image：[riscv-disk.img.gz](http://dist.gem5.org/dist/v21-2/images/riscv/busybox/riscv-disk.img.gz)，然后解压
  ```bash
  $ gzip -cd riscv-disk.img.gz > riscv-disk.img
  ```
* 假设你将`bootloader-vmlinux-5.10`和`riscv-disk.img`放在和`gem5/`同一级目录下

### 修改磁盘镜像
#### 如果你有sudo权限（Podman里的不算）
* 用`mount`配合`loop`挂载，假设挂载到`/mnt/rootfs`目录
  ```bash
  $ sudo mkdir -p /mnt/rootfs
  $ sudo mount -o loop riscv-disk.img /mnt/rootfs
  ```
* 拷贝benchmark，以`hello`为例
  ```bash
  $ cp gem5/hello /mnt/rootfs/root
  ```
* 卸载
  ```bash
  $ sudo umount /mnt/rootfs
  ```
#### 如果你没有sudo权限，但是有Podman
* 启动一个Podman容器，假设你将工作目录映射到`/mnt`
  ```bash
  $ podman run -it -v $(pwd):/mnt ubuntu:20.04
  ```
* 安装`e2tools`工具
  ```bash
  $ apt update
  $ apt install e2tools
  ```
* 拷贝benchmark，以`hello`为例
  ```bash
  $ cd /mnt
  $ e2cp -p gem5/hello riscv-disk.img:/root
  ```
#### 其他命令
* 如果你的benchmark较大，可以对镜像扩容，例如扩容到`300M`
  <b>注：</b>如果你已经用`mount`挂载了镜像，务必先卸载再扩容；用`e2tools`不存在此问题
  ```bash
  $ e2fsck -f riscv-disk.img
  $ resize2fs riscv-disk.img 300M
  ```

### 启动系统并使用m5term连接
* 在其中一个窗口启动gem5，记住如下所示的`port`号（这里是`3456`）
  ```bash
  $ cd gem5
  $ build/RISCV/gem5.opt configs/example/riscv/fs_linux.py \
    --kernel=../bootloader-vmlinux-5.10 \
    --disk-image=../riscv-disk.img
  # ...
  # board.platform.terminal: Listening for connections on port 3456
  # ...
  ```
* 在另一个窗口用`m5term`连接，注意指定正确的`port`
  ```bash
  $ cd gem5
  $ util/term/m5term localhost <port>
  ```
* 约2分钟后，看到如下界面，输入账号和密码即可登录
  ```bash
  # ...
  #  _   _  ____            _     _
  # | | | |/ ___|__ _ _ __ | |   (_)_ __  _   ___  __
  # | | | | |   / _` | '_ \| |   | | '_ \| | | \ \/ /
  # | |_| | |__| (_| | | | | |___| | | | | |_| |>  <
  #  \___/ \____\__,_|_| |_|_____|_|_| |_|\__,_/_/\_\
  # Welcome to RiscV
  #
  # UCanLinux login: root
  # Password: root
  root@UCanLinux:~ #
  ```
* 测试先前拷贝进去的`hello`程序
  ```bash
  $ ./hello
  # Hello, world!
  ```
* <b>注：</b>`m5term`的退出方式为先输入`~`，再输入`.`

## FS模式：高效实验

在上一节中我们只是能够启动操作系统，但还不能做任何有价值的实验，本节将介绍如何使用FS模式运行我们的benchmark，并且高效地完成整个实验流程

### 编译m5
* `m5`是一个在FS容器中操作gem5的命令行小程序，`m5`的原理是执行自定义的指令并被gem5捕获，使得gem5执行一些外部动作，关于`m5`的更多介绍可见 [m5 README](https://github.com/gem5/gem5/tree/stable/util/m5)
* 使用如下命令编译`m5`
  ```bash
  $ cd gem5/util/m5
  $ python3 `which scons` build/RISCV/out/m5
  ```

### 使用Atomic启动Linux
```bash
build/RISCV/gem5.opt configs/example/riscv/fs_linux.py \
  --kernel=riscv-fs/bootloader-vmlinux-5.10 \
  --disk-image=riscv-fs/riscv-disk.img
```
* 系统启动后会自动保存镜像然后退出，下次从保存镜像命令结束的地方开始执行
* 镜像保存位置为`m5out/cpt.<tick_num>/`，`<tick_num>`为退出时的时钟数

### 使用O3CPU运行Benchmark
```bash
time build/RISCV/gem5.opt \
  --debug-flags=ExecEnable,ExecUser,ExecKernel \
  --debug-file=debug.out.gz \
  configs/example/riscv/fs_linux.py \
  --kernel=riscv-fs/bootloader-vmlinux-5.10 \
  --disk-image=riscv-fs/riscv-disk.img \
  -r 1 -I 10000000 --script=riscv-fs/workload.sh
```
* `--debug-file`：实际存储位置为`m5out/debug.out.gz`
* `-r`：恢复checkpoint的版本，如果对gem5的checkpoint机制不了解，请只保留一份镜像，这里填`1`
* `-I`：最大执行指令的数量
* `--script`：需要执行的脚本，注意只能使用`sh`语法，默认工作目录为`/`

### 恢复Checkpoint并执行
* Gshare
  ```bash
  time build/RISCV/gem5.opt \
    configs/example/riscv/fs_linux.py \
    --kernel=riscv-fs/bootloader-vmlinux-5.10 \
    --disk-image=riscv-fs/riscv-disk.img \
    --restore-with-cpu=O3CPU \
    --bp-type=GshareBP --caches \
    --param='system.switch_cpus[0].branchPred.localPredictorSize=16384' \
    --param='system.switch_cpus[0].branchPred.instShiftAmt=1' \
    -r 1 -I 100000000 --script=riscv-fs/workload.sh
  ```
* 查看结果
  ```bash
  grep 'switch_cpus.branchPred.cond' m5out/stats.txt
  ```

## 其他

### 编译CoreMark
* 不限制ISA
  ```bash
  riscv64-unknown-linux-gnu-gcc -O2 -lrt -static -Ilinux -Isimple -I. \
    -DFLAGS_STR=\""-O2 -lrt -static"\" -DITERATIONS=10 \
    core_list_join.c core_main.c core_matrix.c core_state.c core_util.c \
    simple/core_portme.c -o coremark-rv64
  ```
* 禁用RVC
  ```bash
  riscv64-unknown-linux-gnu-gcc -O2 -lrt -static -Ilinux -Isimple -I. -march=rv64g \
    -DFLAGS_STR=\""-O2 -lrt -static"\" -DITERATIONS=10 \
    core_list_join.c core_main.c core_matrix.c core_state.c core_util.c \
    simple/core_portme.c -o coremark-rv64-norvc
  ```
