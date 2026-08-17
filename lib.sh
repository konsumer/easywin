#!/usr/bin/env bash
# shared helpers - sourced by setup.sh, run.sh and run_dxvk.sh
# not meant to be run directly

c_red=$'\033[0;31m'; c_green=$'\033[0;32m'; c_yellow=$'\033[1;33m'; c_reset=$'\033[0m'

log ()  { printf '%s==>%s %s\n' "$c_green" "$c_reset" "$*"; }
warn () { printf '%s==>%s %s\n' "$c_yellow" "$c_reset" "$*" >&2; }
die ()  { printf '%serror:%s %s\n' "$c_red" "$c_reset" "$*" >&2; exit 1; }

# need_cmd <binary> <install hint>
need_cmd () {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency '$1' - $2"
}

# need_file <path> <hint>
need_file () {
  [ -e "$1" ] || die "missing file '$1' - $2"
}

need_kvm () {
  [ -e /dev/kvm ] || die "/dev/kvm not found - enable virtualization (KVM) on this host"
  [ -r /dev/kvm ] && [ -w /dev/kvm ] || die "/dev/kvm exists but is not accessible by $(whoami) - check permissions/groups"
}

# echoes the path to OVMF_CODE, or returns 1 if not found
find_ovmf_code () {
  local f
  for f in /usr/share/edk2/x64/OVMF_CODE.4m.fd /usr/share/OVMF/OVMF_CODE.fd /usr/share/ovmf/x64/OVMF_CODE.fd; do
    [ -e "$f" ] && { echo "$f"; return 0; }
  done
  return 1
}

# echoes the path to the OVMF_VARS template, or returns 1 if not found
find_ovmf_vars_template () {
  local f
  for f in /usr/share/edk2/x64/OVMF_VARS.4m.fd /usr/share/OVMF/OVMF_VARS.fd /usr/share/ovmf/x64/OVMF_VARS.fd; do
    [ -e "$f" ] && { echo "$f"; return 0; }
  done
  return 1
}

# nudge_boot_prompt <monitor_unix_socket>
# Windows install media shows a short-lived "press any key to boot from CD
# or DVD" prompt; under host load the disk-boot path can lose that race and
# the VM just sits there forever waiting for a key nobody will press. This
# backgrounds a few early Enter presses over the QEMU monitor to dismiss it
# reliably, without lingering into the GUI phase where stray keys could do
# something unwanted. Requires the qemu invocation to include
# `-monitor unix:<monitor_unix_socket>,server,nowait`.
nudge_boot_prompt () {
  local sock="$1"
  (
    for _ in 1 2 3 4; do
      sleep 1
      python3 - "$sock" <<'PYEOF' >/dev/null 2>&1
import socket, sys, time
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.connect(sys.argv[1])
    time.sleep(0.1)
    s.recv(4096)
    s.sendall(b"sendkey ret\n")
    time.sleep(0.1)
except Exception:
    pass
finally:
    s.close()
PYEOF
    done
  ) &
}
