import os
import sys
import argparse
import builtins
import m5
from m5.objects import (
    System, SrcClockDomain, VoltageDomain,
    Cache, SystemXBar, L2XBar, MemCtrl, AddrRange, DDR3_1600_8x8,
    AtomicSimpleCPU, SEWorkload, Process, Root)


def print(*args, **kwargs):
    msg = kwargs.get('sep', ' ').join(str(arg) for arg in args)
    if kwargs.get('file', sys.stdout).isatty():
        msg = '\033[93m' + msg + '\033[0m'
    builtins.print(msg, **kwargs)


######################################
#          Argument Parsing          #
######################################

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['profile', 'create', 'restore'])
parser.add_argument('-r', '--checkpoint-restore')
parser.add_argument('-I', '--maxinsts')
parser.add_argument('-i', '--input')
parser.add_argument('-o', '--output')
parser.add_argument('-e', '--errout')
parser.add_argument('--interval', type=int, default=10**8)
parser.add_argument('--warmup', type=int, default=10**7)
parser.add_argument('--switch-cpu', default='O3CPU')
parser.add_argument('binary')
parser.add_argument('options', nargs=argparse.REMAINDER)
args = parser.parse_args()

if args.mode == 'restore':
    if args.maxinsts is not None:
        parser.error('-I/--maxinsts is redundant in restore mode.')
    if args.checkpoint_restore is None:
        parser.error("restore mode requires -r/--checkpoint_restore.")
else:
    if args.checkpoint_restore is not None:
        parser.error('-r/--checkpoint_restore is redundant in '
                     '%s mode.' % args.mode)


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
system.mem_ranges = [AddrRange('8GB')]
# system.mem_ranges = [AddrRange('512MB')]

system.cpu = AtomicSimpleCPU()

system.membus = SystemXBar()

if args.mode == 'restore':
    system.cpu.icache = L1ICache()
    system.cpu.dcache = L1DCache()

    system.cpu.icache.cpu_side = system.cpu.icache_port
    system.cpu.dcache.cpu_side = system.cpu.dcache_port

    system.l2bus = L2XBar()

    system.cpu.icache.mem_side = system.l2bus.cpu_side_ports
    system.cpu.dcache.mem_side = system.l2bus.cpu_side_ports

    # TLB walker caches are necessary to x86 and riscv
    if m5.defines.buildEnv['TARGET_ISA'] in ['x86', 'riscv']:
        system.cpu.itb_walker_cache = PageTableWalkerCache()
        system.cpu.dtb_walker_cache = PageTableWalkerCache()
        system.cpu.mmu.connectWalkerPorts(
            system.cpu.itb_walker_cache.cpu_side,
            system.cpu.dtb_walker_cache.cpu_side)

        system.cpu.itb_walker_cache.mem_side = system.l2bus.cpu_side_ports
        system.cpu.dtb_walker_cache.mem_side = system.l2bus.cpu_side_ports

    system.l2cache = L2Cache()
    system.l2cache.cpu_side = system.l2bus.mem_side_ports

    system.l2cache.mem_side = system.membus.cpu_side_ports

else:
    system.cpu.icache_port = system.membus.cpu_side_ports
    system.cpu.dcache_port = system.membus.cpu_side_ports

system.cpu.createInterruptController()

# For x86 only, make sure the interrupts are connected to the memory
# Note: these are directly connected to the memory bus and are not cached
if m5.defines.buildEnv['TARGET_ISA'] == "x86":
    system.cpu.interrupts[0].pio = system.membus.mem_side_ports
    system.cpu.interrupts[0].int_requestor = system.membus.cpu_side_ports
    system.cpu.interrupts[0].int_responder = system.membus.mem_side_ports

system.mem_ctrl = MemCtrl()
system.mem_ctrl.dram = DDR3_1600_8x8()
system.mem_ctrl.dram.range = system.mem_ranges[0]
system.mem_ctrl.port = system.membus.mem_side_ports

system.system_port = system.membus.cpu_side_ports

system.workload = SEWorkload.init_compatible(args.binary)

process = Process()
process.cmd = [args.binary] + args.options
if args.input is not None:
    process.input = args.input
if args.output is not None:
    process.output = args.output
if args.errout is not None:
    process.errout = args.errout

system.cpu.workload = process
system.cpu.createThreads()


#######################################
#         System Instantiation        #
#######################################

if args.mode == 'profile':
    system.cpu.addSimPointProbe(args.interval)

elif args.mode == 'create':
    spath = os.path.join(m5.options.outdir, 'simpoints.txt')
    wpath = os.path.join(m5.options.outdir, 'weights.txt')
    try:
        with open(spath) as f:
            ss = f.readlines()
        with open(wpath) as f:
            ws = f.readlines()
    except FileNotFoundError:
        print('Either %r or %r not found.' % (spath, wpath),
              'Have you done SimPoint analysis?')
        exit(-1)

    print('Reading simpoints and weights')
    simpoints = []
    for sl, wl in zip(ss, ws):
        s = int(sl.split()[0])
        w = float(wl.split()[0])
        simpoints.append((s, w))
    simpoints.sort()

    simpoint_start_insts = []
    for s, w in simpoints:
        insts = s * args.interval
        if insts > args.warmup:
            insts -= args.warmup
        else:
            insts = 0
        simpoint_start_insts.append(insts)

    print('Found %d start points' % len(simpoint_start_insts))
    system.cpu.simpoint_start_insts = simpoint_start_insts

else:
    cpu_class = getattr(m5.objects, args.switch_cpu)

    switch_cpu = cpu_class(switched_out=True, cpu_id=0)
    switch_cpu.workload = system.cpu.workload
    switch_cpu.clk_domain = system.cpu.clk_domain
    switch_cpu.progress_interval = system.cpu.progress_interval
    switch_cpu.isa = system.cpu.isa

    simpoint_start_insts = []
    if args.warmup:
        simpoint_start_insts.append(args.warmup)
    simpoint_start_insts.append(args.warmup + args.interval)
    switch_cpu.simpoint_start_insts = simpoint_start_insts

    switch_cpu.createThreads()
    system.switch_cpu = switch_cpu

if args.maxinsts is not None:
    system.cpu.max_insts_any_thread = args.maxinsts

root = Root(full_system=False, system=system)

if args.mode == 'restore':
    ckpt_dir = os.path.join(m5.options.outdir, args.checkpoint_restore)
    print('Restoring checkpoint %r' % ckpt_dir)
    m5.instantiate(ckpt_dir)
else:
    print('Instantiating')
    m5.instantiate()


#######################################
#           Real Simulation           #
#######################################

CAUSE_SIMPOINT = 'simpoint starting point found'

if args.mode == 'profile':
    print('Simulating with profiling')
    exit_event = m5.simulate()

elif args.mode == 'create':
    for (s, w), ssi in zip(simpoints, simpoint_start_insts):
        print('Simulating until %d insts' % ssi)
        exit_event = m5.simulate()
        exit_cause = exit_event.getCause()
        if exit_cause != CAUSE_SIMPOINT:
            break

        # Create checkpoint only if we are at start point
        insts = s * args.interval
        warmup = args.warmup if insts > args.warmup else 0

        ckpt = 'cpt.insts_%d.interval_%d.warmup_%d.weight_%r'
        ckpt = ckpt % (insts, args.interval, warmup, w)
        ckpt_dir = os.path.join(m5.options.outdir, ckpt)
        print('Creating checkpoint %r' % (ckpt_dir))
        m5.checkpoint(ckpt_dir)

else:
    print('Pre-warming up the system for 10000 ticks')
    m5.simulate(10000)

    # Switch CPUs
    print('Switching CPUs:', system.cpu.type, '->', system.switch_cpu.type)
    switch_cpu_list = [(system.cpu, system.switch_cpu)]
    m5.switchCpus(system, switch_cpu_list)

    # Warmup
    if args.warmup:
        print('Warming up for %d insts' % args.warmup)
        exit_event = m5.simulate()
        exit_cause = exit_event.getCause()
        if exit_cause == CAUSE_SIMPOINT:
            m5.stats.dump()
            m5.stats.reset()

    # Simulate
    if (not args.warmup) or exit_cause == CAUSE_SIMPOINT:
        print('Simulating for %d insts' % args.interval)
        exit_event = m5.simulate()
        exit_cause = exit_event.getCause()
        if exit_cause == CAUSE_SIMPOINT:
            print('Done running SimPoint')

print('Exiting @ tick %d because %s' % (m5.curTick(), exit_event.getCause()))
