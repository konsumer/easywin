#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

usage () { echo "usage: $0 <disk.qcow2>" >&2; exit 1; }
[ $# -eq 1 ] || usage
DISK="$1"
RAM="${RAM:-8G}"
SMP="${SMP:-4}"

need_cmd qemu-system-x86_64 "install qemu (e.g. 'pacman -S qemu-desktop' / 'apt install qemu-system-x86')"
need_file "$DISK" "run setup.sh first to create it"
need_kvm

OVMF_CODE="$(find_ovmf_code)" || die "OVMF firmware not found - install it (e.g. 'pacman -S edk2-ovmf' / 'apt install ovmf')"
OVMF_VARS_TEMPLATE="$(find_ovmf_vars_template)" || die "OVMF_VARS firmware not found - install it (e.g. 'pacman -S edk2-ovmf' / 'apt install ovmf')"

VARS_FILE="$DISK.vars.fd"
[ -e "$VARS_FILE" ] || cp "$OVMF_VARS_TEMPLATE" "$VARS_FILE"

# virtio-blk/virtio-net for real throughput; setup.sh stages their drivers
# automatically (re-run it on a fresh disk if this hangs/BSODs on boot). GPU
# acceleration is a separate, unstable step - see run_dxvk.sh.
log "starting $DISK"
exec qemu-system-x86_64 \
  -name easywin \
  -machine q35 -accel kvm -cpu host \
  -smp "$SMP" -m "$RAM" \
  -drive file="$OVMF_CODE",if=pflash,format=raw,readonly=on \
  -drive file="$VARS_FILE",if=pflash,format=raw \
  -drive file="$DISK",if=virtio,format=qcow2 \
  -device virtio-net-pci,netdev=net0 -netdev user,id=net0 \
  -vga std \
  -usb -device usb-tablet
