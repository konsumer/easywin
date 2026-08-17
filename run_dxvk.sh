#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

usage () { echo "usage: $0 <disk.qcow2>" >&2; exit 1; }
[ $# -eq 1 ] || usage
DISK="$1"
RAM="${RAM:-8G}"
SMP="${SMP:-4}"
HOSTMEM="${HOSTMEM:-4G}"

need_cmd qemu-system-x86_64 "install qemu (e.g. 'pacman -S qemu-desktop' / 'apt install qemu-system-x86')"
need_file "$DISK" "run setup.sh first to create it"
need_kvm

OVMF_CODE="$(find_ovmf_code)" || die "OVMF firmware not found - install it (e.g. 'pacman -S edk2-ovmf' / 'apt install ovmf')"
OVMF_VARS_TEMPLATE="$(find_ovmf_vars_template)" || die "OVMF_VARS firmware not found - install it (e.g. 'pacman -S edk2-ovmf' / 'apt install ovmf')"

qemu-system-x86_64 -device virtio-vga-gl,help 2>&1 | grep -q '^  venus=' \
  || die "this qemu build's virtio-vga-gl lacks 'venus' support"
ls /usr/share/vulkan/icd.d/*.json >/dev/null 2>&1 \
  || die "no Vulkan ICD found on host (/usr/share/vulkan/icd.d) - install your GPU's vulkan driver"
qemu-system-x86_64 -display help 2>&1 | grep -qx gtk \
  || die "qemu was built without the gtk display backend (needed for gl=on rendering)"

VARS_FILE="$DISK.vars.fd"
[ -e "$VARS_FILE" ] || cp "$OVMF_VARS_TEMPLATE" "$VARS_FILE"

warn "EXPERIMENTAL: this uses an unstable, unofficial Windows Vulkan/venus"
warn "driver (github.com/arehnman/kvm-guest-drivers-windows) that you have"
warn "to build and install yourself - it is known to crash/hang the guest."
warn "Inside Windows you also need DXVK (github.com/doitsujin/dxvk) dropped"
warn "into whatever game/app you want accelerated. This uses virtio-blk/"
warn "virtio-net like run.sh (setup.sh stages those drivers automatically)."
warn "Use run.sh instead for a stable, non-accelerated VM."

log "starting $DISK (experimental venus GPU acceleration)"
exec qemu-system-x86_64 \
  -name easywin-dxvk \
  -machine q35 -accel kvm -cpu host \
  -smp "$SMP" -m "$RAM" \
  -object memory-backend-memfd,id=mem0,size="$RAM",share=on \
  -numa node,memdev=mem0 \
  -drive file="$OVMF_CODE",if=pflash,format=raw,readonly=on \
  -drive file="$VARS_FILE",if=pflash,format=raw \
  -drive file="$DISK",if=virtio,format=qcow2 \
  -device virtio-net-pci,netdev=net0 -netdev user,id=net0 \
  -vga none -device virtio-vga-gl,hostmem="$HOSTMEM",blob=on,venus=on \
  -display gtk,gl=on \
  -usb -device usb-tablet
