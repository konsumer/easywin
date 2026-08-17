#!/usr/bin/env python3
'''
Create a qcow2 disk and install Windows 11 onto it, fully unattended, then
inject virtio-blk/virtio-net drivers into the installed image so run.py gets
real disk/network throughput.

usage: ./setup.py <source.iso> <disk.qcow2> [size]

run.py imports the small helpers at the top of this file, so those two are
the only scripts here.
'''

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / '.cache'
VIRTIO_WIN_URL = 'https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso'

OVMF_CODE_PATHS = [
  '/usr/share/edk2/x64/OVMF_CODE.4m.fd',
  '/usr/share/OVMF/OVMF_CODE.fd',
  '/usr/share/ovmf/x64/OVMF_CODE.fd',
]
OVMF_VARS_PATHS = [
  '/usr/share/edk2/x64/OVMF_VARS.4m.fd',
  '/usr/share/OVMF/OVMF_VARS.fd',
  '/usr/share/ovmf/x64/OVMF_VARS.fd',
]

OVMF_HINT = "OVMF firmware not found - install it (e.g. 'pacman -S edk2-ovmf' / 'apt install ovmf')"

# ---------------------------------------------------------------- helpers --
# (shared with run.py)


def log (msg):
  print(f'\033[0;32m==>\033[0m {msg}')


def warn (msg):
  print(f'\033[1;33m==>\033[0m {msg}', file=sys.stderr)


def die (msg):
  print(f'\033[0;31merror:\033[0m {msg}', file=sys.stderr)
  sys.exit(1)


def need_cmd (cmd, hint):
  if not shutil.which(cmd):
    die(f"missing dependency '{cmd}' - {hint}")


def need_file (path, hint):
  if not os.path.exists(path):
    die(f"missing file '{path}' - {hint}")


def need_kvm ():
  if not os.path.exists('/dev/kvm'):
    die('/dev/kvm not found - enable virtualization (KVM) on this host')
  if not os.access('/dev/kvm', os.R_OK | os.W_OK):
    die('/dev/kvm exists but is not accessible by this user - check permissions/groups')


def find_ovmf ():
  '''returns (OVMF_CODE, OVMF_VARS template) paths'''
  code = next((p for p in OVMF_CODE_PATHS if os.path.exists(p)), None)
  varsfd = next((p for p in OVMF_VARS_PATHS if os.path.exists(p)), None)
  if not code or not varsfd:
    die(OVMF_HINT)
  return code, varsfd


def qemu_base (name, ovmf_code, vars_file, ram, smp):
  '''the qemu args every VM here shares'''
  return [
    'qemu-system-x86_64',
    '-name', name,
    '-machine', 'q35', '-accel', 'kvm', '-cpu', 'host',
    '-smp', str(smp), '-m', str(ram),
    '-drive', f'file={ovmf_code},if=pflash,format=raw,readonly=on',
    '-drive', f'file={vars_file},if=pflash,format=raw',
  ]


class Monitor:
  '''
  Tiny client for QEMU's monitor socket, which lets us press keys inside the
  VM from out here. Requires the qemu invocation to include
  `-monitor unix:<path>,server,nowait`.
  '''

  # characters whose qemu keyname isn't just the character itself
  KEYMAP = {
    ' ': 'spc', '=': 'equal', ':': 'shift-semicolon', '\\': 'backslash',
    '.': 'dot', '-': 'minus', '_': 'shift-minus', '*': 'shift-8',
    '/': 'slash', ',': 'comma', '!': 'shift-1', '(': 'shift-9', ')': 'shift-0',
    '"': 'shift-apostrophe', "'": 'apostrophe', '+': 'shift-equal',
    '|': 'shift-backslash', ';': 'semicolon', '<': 'shift-comma', '>': 'shift-dot',
    '#': 'shift-3', '&': 'shift-7', '%': 'shift-5', '@': 'shift-2', '$': 'shift-4',
  }

  def __init__ (self, path):
    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    self.sock.settimeout(5)
    self.sock.connect(str(path))
    time.sleep(0.2)
    self._drain()

  def __enter__ (self):
    return self

  def __exit__ (self, *_):
    self.close()

  def _drain (self):
    try:
      self.sock.recv(65536)
    except OSError:
      pass

  def cmd (self, text):
    self.sock.sendall((text + '\n').encode())
    time.sleep(0.04)
    self._drain()

  def key (self, name):
    self.cmd(f'sendkey {name}')

  def typeline (self, line):
    '''types a line of text into the guest, then presses enter'''
    for c in line:
      if c in self.KEYMAP:
        self.key(self.KEYMAP[c])
      elif c.isupper():
        self.key(f'shift-{c.lower()}')
      else:
        self.key(c)
    self.key('ret')

  def close (self):
    self.sock.close()


def background (fn, *args):
  threading.Thread(target=fn, args=args, daemon=True).start()


# ------------------------------------------------------------------ setup --


def download (url, dest):
  '''download to dest (via a .part file) with a simple progress line'''
  dest = Path(dest)
  part = dest.with_suffix(dest.suffix + '.part')
  with urllib.request.urlopen(url) as res, open(part, 'wb') as out:
    total = int(res.headers.get('content-length', 0))
    done = 0
    while True:
      chunk = res.read(1 << 20)
      if not chunk:
        break
      out.write(chunk)
      done += len(chunk)
      pct = f'{done * 100 // total}%' if total else f'{done >> 20}M'
      print(f'  {pct}', end='\r', flush=True)
  print()
  part.rename(dest)


def nudge_boot_prompt (sock_path):
  '''
  Windows install media shows a short-lived "press any key to boot from CD or
  DVD" prompt; under host load the disk-boot path can lose that race and the
  VM just sits there forever waiting for a key nobody will press. This sends a
  few early Enter presses to dismiss it reliably, without lingering into the
  GUI phase where stray keys could do something unwanted.
  '''
  def run ():
    for _ in range(4):
      time.sleep(1)
      try:
        with Monitor(sock_path) as mon:
          mon.key('ret')
      except OSError:
        pass

  background(run)


DRIVER_COMMANDS = [
  r'cmd /c for %D in (D E F G H) do @if exist %D:\viostor\nul dism /image:C:\ /add-driver /driver:%D:\viostor\w11\amd64\viostor.inf',
  r'cmd /c for %D in (D E F G H) do @if exist %D:\NetKVM\nul dism /image:C:\ /add-driver /driver:%D:\NetKVM\w11\amd64\netkvm.inf',
]


def inject_drivers (sock_path):
  '''
  Drives WinPE (the Windows Setup boot environment) over the QEMU monitor to
  inject virtio-blk/virtio-net drivers into the offline Windows image with
  DISM, then shuts the VM down. Windows Setup's answer-file engine rejects a
  windowsPE-pass RunSynchronous block with no ImageInstall/UserData (fails
  with error 0x80070002 - 0x40030 right after the language screen), so this
  drives the same Shift+F10 rescue-console commands a person would type by
  hand instead.

  Retries the whole sequence a few times spaced well apart, since we can't see
  the screen to know exactly when Setup is ready for input; DISM's /add-driver
  is idempotent (a second pass on an already-installed driver is a harmless
  no-op), and once wpeutil shutdown succeeds the monitor socket stops
  accepting connections, which ends the retry loop.
  '''
  def run ():
    for attempt in range(6):
      time.sleep(20 if attempt == 0 else 45)
      try:
        with Monitor(sock_path) as mon:
          mon.key('shift-f10')
          time.sleep(2)
          for line in DRIVER_COMMANDS:
            mon.typeline(line)
            time.sleep(10)
          mon.typeline('wpeutil shutdown')
      except OSError:
        return

  background(run)


def make_autounattend_iso (workdir, username, image_index):
  '''fill in autounattend.xml and wrap it in its own little iso'''
  xml = (HERE / 'autounattend.xml').read_text()
  xml = xml.replace('__USERNAME__', username).replace('__IMAGE_INDEX__', str(image_index))
  xml_path = workdir / 'autounattend.xml'
  xml_path.write_text(xml)
  iso_path = workdir / 'autounattend.iso'
  subprocess.run(
    ['xorriso', '-as', 'mkisofs', '-o', str(iso_path), '-V', 'AUTOUNATTEND', '-J', '-R', str(xml_path)],
    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
  )
  return iso_path


def main ():
  parser = argparse.ArgumentParser(description='install Windows 11 into a qcow2 disk, unattended')
  parser.add_argument('iso', help='path to your Windows 11 installer ISO')
  parser.add_argument('disk', help='qcow2 disk to create')
  parser.add_argument('size', nargs='?', default='64G', help='disk size (default: 64G)')
  parser.add_argument('--user', default=os.environ.get('USERNAME', 'User'), help='local account to create')
  parser.add_argument('--image-index', default=os.environ.get('IMAGE_INDEX', '1'),
                      help='edition to install if the ISO has several (default: 1)')
  parser.add_argument('--ram', default=os.environ.get('RAM', '8G'))
  parser.add_argument('--smp', default=os.environ.get('SMP', '4'))
  args = parser.parse_args()

  need_cmd('qemu-system-x86_64', "install qemu (e.g. 'pacman -S qemu-desktop' / 'apt install qemu-system-x86')")
  need_cmd('qemu-img', "install qemu-img (e.g. 'pacman -S qemu-img' / 'apt install qemu-utils')")
  need_cmd('xorriso', "install xorriso (e.g. 'pacman -S libisoburn' / 'apt install xorriso')")
  need_file(args.iso, 'pass the path to your Windows 11 installer ISO')
  need_kvm()

  ovmf_code, ovmf_vars_template = find_ovmf()

  if os.path.exists(args.disk):
    die(f'{args.disk} already exists - remove it first if you want to reinstall')

  CACHE_DIR.mkdir(exist_ok=True)
  virtio_iso = CACHE_DIR / 'virtio-win.iso'
  if not virtio_iso.exists():
    log(f'downloading virtio-win drivers (first run only, cached in {CACHE_DIR})')
    download(VIRTIO_WIN_URL, virtio_iso)

  workdir = Path(tempfile.mkdtemp(prefix='build.', dir=CACHE_DIR))
  try:
    autounattend_iso = make_autounattend_iso(workdir, args.user, args.image_index)

    log(f'creating {args.disk} ({args.size})')
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', args.disk, args.size],
                   check=True, stdout=subprocess.DEVNULL)

    vars_file = f'{args.disk}.vars.fd'
    shutil.copyfile(ovmf_vars_template, vars_file)

    log(f'installing unattended (user: {args.user}, image index: {args.image_index}) -')
    log(f'this partitions the {args.size} disk, installs Windows, creates a local')
    log('account, and shuts down when done - no interaction needed. Pass')
    log('--image-index N if your ISO has multiple editions and it picks the')
    log('wrong one.')
    # the Windows ISO is an isohybrid image (GPT+ESP, meant to be booted as a
    # plain disk like a dd'd USB stick), so a copy of it is attached as a
    # bootable disk for OVMF. But Windows' own volume manager can't read the
    # ISO9660/UDF payload (install.wim etc.) off that disk-shaped attachment -
    # it only shows an empty "CD-ROM, No Media" - so the same ISO is ALSO
    # attached as a plain cdrom, which is what Windows actually reads files
    # from. autounattend.xml is delivered on its own small cdrom (Setup only
    # auto-detects it on removable media, not a plain attached disk). The
    # target disk stays plain/AHCI: Windows Setup's automated XML-driven
    # DiskConfiguration/ImageInstall partitions and installs onto it fine,
    # even though the *interactive* disk picker can't see AHCI disks here -
    # those are two different code paths, only one of them broken.
    install_sock = workdir / 'install.sock'
    nudge_boot_prompt(install_sock)
    subprocess.run(qemu_base('easywin-setup', ovmf_code, vars_file, args.ram, args.smp) + [
      '-drive', f'file={args.disk},format=qcow2,media=disk',
      '-drive', f'if=none,id=src,file={args.iso},format=raw,readonly=on',
      '-device', 'virtio-blk-pci,drive=src,bootindex=1',
      '-drive', f'file={args.iso},media=cdrom,format=raw,readonly=on',
      '-drive', f'file={autounattend_iso},media=cdrom,format=raw,readonly=on',
      '-device', 'e1000,netdev=net0', '-netdev', 'user,id=net0',
      '-monitor', f'unix:{install_sock},server,nowait',
      '-vga', 'std',
      '-usb', '-device', 'usb-tablet',
    ], check=True)

    log(f'install done - staging virtio-blk/virtio-net drivers into {args.disk} -')
    log('this boots WinPE again to run DISM offline driver injection, and')
    log('shuts down when done - no interaction needed.')
    # run.py boots with virtio-blk/virtio-net for real throughput, which
    # Windows doesn't have drivers for out of the box, and which Windows
    # Setup's own answer-file engine can't inject mid-install. So this
    # re-boots the same install media into WinPE and scripts the DISM
    # commands - see inject_drivers above. The now-installed target disk has
    # a bootable Windows on it, so unlike a blank disk it needs an explicit
    # bootindex, or it can unpredictably win boot priority over the install
    # media instead of WinPE coming up.
    stage_sock = workdir / 'stage.sock'
    nudge_boot_prompt(stage_sock)
    inject_drivers(stage_sock)
    subprocess.run(qemu_base('easywin-drivers', ovmf_code, vars_file, args.ram, args.smp) + [
      '-drive', f'if=none,id=tgt,file={args.disk},format=qcow2',
      '-device', 'ide-hd,drive=tgt,bootindex=2',
      '-drive', f'if=none,id=src,file={args.iso},format=raw,readonly=on',
      '-device', 'virtio-blk-pci,drive=src,bootindex=1',
      '-drive', f'file={args.iso},media=cdrom,format=raw,readonly=on',
      '-drive', f'file={virtio_iso},media=cdrom,format=raw,readonly=on',
      '-device', 'e1000,netdev=net0', '-netdev', 'user,id=net0',
      '-monitor', f'unix:{stage_sock},server,nowait',
      '-vga', 'std',
      '-usb', '-device', 'usb-tablet',
    ], check=True)
  finally:
    shutil.rmtree(workdir, ignore_errors=True)

  log(f'setup finished - your disk is ready: {args.disk} (run.py will use it)')


if __name__ == '__main__':
  main()
