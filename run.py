#!/usr/bin/env python3
'''
Boot a disk made by setup.py.

usage: ./run.py <disk.qcow2> [--gpu]
'''

import argparse
import glob
import os
import shutil
import subprocess

from setup import die, find_ovmf, log, need_cmd, need_file, need_kvm, qemu_base, warn


def qemu_supports (args, wanted):
  '''run a qemu help query and look for a line starting with `wanted`'''
  out = subprocess.run(['qemu-system-x86_64'] + args, capture_output=True, text=True)
  return any(line.strip().startswith(wanted) for line in (out.stdout + out.stderr).splitlines())


def gpu_args (ram, hostmem):
  '''display/memory args for experimental virtio-gpu + venus acceleration'''
  if not qemu_supports(['-device', 'virtio-vga-gl,help'], 'venus='):
    die("this qemu build's virtio-vga-gl lacks 'venus' support")
  if not glob.glob('/usr/share/vulkan/icd.d/*.json'):
    die('no Vulkan ICD found on host (/usr/share/vulkan/icd.d) - install your GPU\'s vulkan driver')
  if not qemu_supports(['-display', 'help'], 'gtk'):
    die('qemu was built without the gtk display backend (needed for gl=on rendering)')

  # virtio-vga-gl boots fine with no guest driver at all (Windows just falls
  # back to a generic display), so this is safe to leave on for everyday use.
  # The actual instability the upstream driver project warns about only shows
  # up once that driver is doing real rendering work - see below.
  warn('--gpu: EXPERIMENTAL acceleration. Windows boots fine without any')
  warn('extra driver (falls back to a generic display), but real GPU use')
  warn('needs an unstable, unofficial Windows Vulkan/venus driver')
  warn('(github.com/arehnman/kvm-guest-drivers-windows) that you build and')
  warn('install yourself inside the guest - it can crash/hang once it\'s')
  warn('doing real rendering work. You\'ll also need DXVK')
  warn('(github.com/doitsujin/dxvk) dropped into whatever game/app you want')
  warn('accelerated.')
  return [
    '-object', f'memory-backend-memfd,id=mem0,size={ram},share=on',
    '-numa', 'node,memdev=mem0',
    '-vga', 'none',
    '-device', f'virtio-vga-gl,hostmem={hostmem},blob=on,venus=on',
    '-display', 'gtk,gl=on',
  ]


def main ():
  parser = argparse.ArgumentParser(description='boot a disk made by setup.py')
  parser.add_argument('disk', help='qcow2 disk to boot')
  parser.add_argument('--gpu', action='store_true', default=os.environ.get('GPU') == '1',
                      help='experimental virtio-gpu/venus acceleration')
  parser.add_argument('--ram', default=os.environ.get('RAM', '8G'))
  parser.add_argument('--smp', default=os.environ.get('SMP', '4'))
  parser.add_argument('--hostmem', default=os.environ.get('HOSTMEM', '4G'),
                      help='virtio-gpu host memory window (--gpu only)')
  args = parser.parse_args()

  need_cmd('qemu-system-x86_64', "install qemu (e.g. 'pacman -S qemu-desktop' / 'apt install qemu-system-x86')")
  need_file(args.disk, 'run setup.py first to create it')
  need_kvm()

  ovmf_code, ovmf_vars_template = find_ovmf()
  vars_file = f'{args.disk}.vars.fd'
  if not os.path.exists(vars_file):
    shutil.copyfile(ovmf_vars_template, vars_file)

  display_args = gpu_args(args.ram, args.hostmem) if args.gpu else ['-vga', 'std']

  log(f'starting {args.disk}' + (' (GPU acceleration enabled)' if args.gpu else ''))
  # virtio-blk/virtio-net for real throughput; setup.py stages their drivers
  # automatically (re-run it on a fresh disk if this hangs/BSODs on boot).
  cmd = qemu_base('easywin', ovmf_code, vars_file, args.ram, args.smp) + [
    '-drive', f'file={args.disk},if=virtio,format=qcow2',
    '-device', 'virtio-net-pci,netdev=net0', '-netdev', 'user,id=net0',
    '-usb', '-device', 'usb-tablet',
  ] + display_args
  os.execvp(cmd[0], cmd)


if __name__ == '__main__':
  main()
