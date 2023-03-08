import os
import sys
import builtins
import argparse
import m5
from m5.objects import Cache, MemCtrl, SystemXBar, L2XBar, DDR3_1600_8x8, AddrRange
from m5.objects import AtomicSimpleCPU, O3CPU
from m5.objects import Process, Root, SEWorkload, System, SrcClockDomain, VoltageDomain


def print(*args):
    msg = ' '.join(str(arg) for arg in args)
    builtins.print('\033[93m' + msg + '\033[0m')


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


binary = os.path.join(os.path.dirname(sys.argv[0]), './hello')

system = System()

system.clk_domain = SrcClockDomain()
system.clk_domain.clock = '2.66GHz'
system.clk_domain.voltage_domain = VoltageDomain()

system.mem_mode = 'atomic'
system.mem_ranges = [AddrRange('512MB')]

system.cpu = AtomicSimpleCPU()

system.cpu.icache = L1ICache()
system.cpu.dcache = L1DCache()

system.cpu.icache.cpu_side = system.cpu.icache_port
system.cpu.dcache.cpu_side = system.cpu.dcache_port

# RISCV requires TLB walker cache
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

# For x86 only, make sure the interrupts are connected to the memory
# Note: these are directly connected to the memory bus and are not cached
if m5.defines.buildEnv['TARGET_ISA'] == "x86":
    system.cpu.interrupts[0].pio = system.membus.mem_side_ports
    system.cpu.interrupts[0].int_requestor = system.membus.cpu_side_ports
    system.cpu.interrupts[0].int_responder = system.membus.mem_side_ports

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

# system.cpu.max_insts_any_thread = 10000

mode = 'repeat'

if mode == 'single':
    pass

elif mode == 'repeat':
    switch_cpu = O3CPU(switched_out=True, cpu_id=0)
    switch_cpu.workload = system.cpu.workload
    switch_cpu.clk_domain = system.cpu.clk_domain
    switch_cpu.progress_interval = system.cpu.progress_interval
    switch_cpu.isa = system.cpu.isa

    switch_cpu.createThreads()
    system.switch_cpus = [switch_cpu]

root = Root(full_system=False, system=system)
m5.instantiate()

print("Beginning simulation")

if mode == 'single':
    exit_event = m5.simulate()

elif mode == 'repeat':
    maxtick = 0xffffffffffffffff
    switch_freq = 10000000
    switch_cpu_list = [(system.cpu, system.switch_cpus[0])]
    last_tick = 0

    while True:
        switch_cpu_list[0][0].scheduleInstStop(0, 50000, 'slice')
        exit_event = m5.simulate(ticks=switch_freq)
        exit_cause = exit_event.getCause()

        if exit_cause not in ('slice', 'simulate() limit reached'):
            break

        print('Pause @ tick %i because %s' % (m5.curTick(), exit_cause))
        print('Execute with %s spent %d' % (switch_cpu_list[0][0], m5.curTick() - last_tick))
        print('Switch %s -> %s' % (switch_cpu_list[0]))
        last_tick = m5.curTick()

        m5.switchCpus(system, switch_cpu_list)

        switch_cpu_list = [tuple(reversed(i)) for i in switch_cpu_list]

        # if (maxtick - m5.curTick()) <= switch_freq:
        #    exit_event = m5.simulate(maxtick - m5.curTick())
        #    break

print('Exiting @ tick %i because %s' % (m5.curTick(), exit_event.getCause()))
