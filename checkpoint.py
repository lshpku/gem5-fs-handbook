import os
import builtins
import argparse
import subprocess
import m5
from m5.objects import (
    System, SrcClockDomain, VoltageDomain,
    Cache, SystemXBar, L2XBar, MemCtrl, AddrRange, DDR3_1600_8x8,
    AtomicSimpleCPU, O3CPU, SEWorkload, Process, Root)


def print(*args, **kwargs):
    msg = ' '.join(str(arg) for arg in args)
    builtins.print('\033[93m' + msg + '\033[0m', **kwargs)


# Parse argument
parser = argparse.ArgumentParser()
parser.add_argument('action', choices=[
    'create_by_fixed_ticks',
    'create_by_fixed_insts',
    'restore',
    'restore_and_switch',
    'switch_repeatedly'])
args = parser.parse_args()
action = args.action

# Compile helloworld executable
c_src = 'hello.c'
binary = 'hello.exe'
if not os.path.exists(binary):
    print('Compile helloworld executable')
    with open(c_src, 'w') as f:
        f.write('''#include <stdio.h>
int fib(int i) { return i < 2 ? 1 : fib(i - 2) + fib(i - 1); }
int main() {
    printf("fib(20) = %d\\n", fib(20));
}''')
    cmd = ['riscv64-unknown-elf-gcc', '-O2', '-static', c_src, '-o', binary]
    p = subprocess.Popen(cmd)
    if p.wait():
        exit(p.returncode)


#######################################
#          Cache Definitions          #
#######################################


class L1Cache(Cache):
    assoc = 2
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 4
    tgts_per_mshr = 20


class L1ICache(L1Cache):
    size = '16kB'


class L1DCache(L1Cache):
    size = '64kB'


class L2Cache(Cache):
    size = '256kB'
    assoc = 8
    tag_latency = 20
    data_latency = 20
    response_latency = 20
    mshrs = 20
    tgts_per_mshr = 12


class PageTableWalkerCache(Cache):
    assoc = 2
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 10
    size = '1kB'
    tgts_per_mshr = 12


#######################################
#         System Configuration        #
#######################################

system = System()

system.clk_domain = SrcClockDomain()
system.clk_domain.clock = '2GHz'
system.clk_domain.voltage_domain = VoltageDomain()

system.mem_mode = 'atomic'
system.mem_ranges = [AddrRange('512MB')]

# Use AtomicSimpleCPU initially (we will switch to O3CPU later)
system.cpu = AtomicSimpleCPU()

system.cpu.icache = L1ICache()
system.cpu.dcache = L1DCache()

system.cpu.icache.cpu_side = system.cpu.icache_port
system.cpu.dcache.cpu_side = system.cpu.dcache_port

# Note: TLB walker caches are necessary to RISC-V
system.cpu.itb_walker_cache = PageTableWalkerCache()
system.cpu.dtb_walker_cache = PageTableWalkerCache()
system.cpu.mmu.connectWalkerPorts(
    system.cpu.itb_walker_cache.cpu_side,
    system.cpu.dtb_walker_cache.cpu_side)

system.l2bus = L2XBar()

system.cpu.icache.mem_side = system.l2bus.cpu_side_ports
system.cpu.dcache.mem_side = system.l2bus.cpu_side_ports

system.cpu.itb_walker_cache.mem_side = system.l2bus.cpu_side_ports
system.cpu.dtb_walker_cache.mem_side = system.l2bus.cpu_side_ports

system.l2cache = L2Cache()
system.l2cache.cpu_side = system.l2bus.mem_side_ports

system.membus = SystemXBar()

system.l2cache.mem_side = system.membus.cpu_side_ports

system.cpu.createInterruptController()

system.system_port = system.membus.cpu_side_ports

system.mem_ctrl = MemCtrl()
system.mem_ctrl.dram = DDR3_1600_8x8()
system.mem_ctrl.dram.range = system.mem_ranges[0]
system.mem_ctrl.port = system.membus.mem_side_ports

system.workload = SEWorkload.init_compatible(binary)

process = Process()
process.cmd = [binary]
system.cpu.workload = process
system.cpu.createThreads()

# Before system instantiation, add an O3CPU as switch_cpu to system if we
# will switch. It should copy key settings from the original cpu
if 'switch' in action:
    switch_cpu = O3CPU(switched_out=True, cpu_id=0)
    switch_cpu.workload = system.cpu.workload
    switch_cpu.clk_domain = system.cpu.clk_domain
    switch_cpu.progress_interval = system.cpu.progress_interval
    switch_cpu.isa = system.cpu.isa

    switch_cpu.createThreads()
    system.switch_cpu = switch_cpu

# Set ckpt_dir if we are restoring from some checkpoint
ckpt_dir = None
m5out = m5.options.outdir
if 'restore' in action:
    ckpt_dir = os.path.join(m5out, 'ckpt.001')
    if not os.path.exists(ckpt_dir):
        print("You haven't create any checkpoint yet! Abort")
        exit(-1)

# Instantiate system
root = Root(full_system=False, system=system)
if ckpt_dir is None:
    print('Instantiate')
    m5.instantiate()
else:
    print('Restore from checkpoint')
    m5.instantiate(ckpt_dir)


#######################################
#           Real Simulation           #
#######################################

if action == 'create_by_fixed_ticks':
    interval_ticks = 20000000
    i = 1

    while True:
        print('Simulate for %d ticks' % interval_ticks)
        exit_event = m5.simulate(interval_ticks)
        if exit_event.getCause() != 'simulate() limit reached':
            break

        print('Pause @ tick', m5.curTick())
        print('Create checkpoint', i)
        m5.checkpoint(os.path.join(m5out, 'ckpt.%03d' % i))
        i += 1

elif action == 'create_by_fixed_insts':
    interval_insts = 20000
    tid = 0  # thread id, should be 0 since we have only one thread
    event_str = 'inst stop'  # any unique string is fine
    i = 1

    while True:
        print('Simulate for %d insts' % interval_insts)
        system.cpu.scheduleInstStop(tid, interval_insts, event_str)
        exit_event = m5.simulate()
        if exit_event.getCause() != event_str:
            break

        print('Pause @ tick', m5.curTick())
        print('Create checkpoint', i)
        m5.checkpoint(os.path.join(m5out, 'ckpt.%03d' % i))
        i += 1

elif action == 'restore':
    print('Resume simulation')
    exit_event = m5.simulate()

elif action == 'restore_and_switch':
    print('Warmup for 10000 ticks')
    m5.simulate(10000)

    print('Switch @ tick', m5.curTick())
    switch_cpu_list = [(system.cpu, system.switch_cpu)]
    m5.switchCpus(system, switch_cpu_list)

    print('Simulate on switch_cpu')
    exit_event = m5.simulate()

elif action == 'switch_repeatedly':
    interval_insts = 10000
    switch_cpu_list = [(system.cpu, system.switch_cpu)]
    tid = 0
    event_str = 'inst stop'

    while True:
        print('Simulate for %d insts' % interval_insts)
        curr_cpu = switch_cpu_list[0][0]
        curr_cpu.scheduleInstStop(tid, interval_insts, event_str)
        exit_event = m5.simulate()
        if exit_event.getCause() != event_str:
            break

        print('Pause @ tick', m5.curTick())
        print('Switch %s -> %s' % (switch_cpu_list[0]))
        m5.switchCpus(system, switch_cpu_list)
        switch_cpu_list = [(p[1], p[0]) for p in switch_cpu_list]

print('Exiting @ tick %d because %s' % (m5.curTick(), exit_event.getCause()))
