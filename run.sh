#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

usage () { echo "usage: $0 <disk.qcow2>" >&2; exit 1; }
[ $# -eq 1 ] || usage
DISK="$1"
RAM="${RAM:-8G}"
SMP="${SMP:-4}"
GPU="${GPU:-0}"
HOSTMEM="${HOSTMEM:-4G}"

need_cmd qemu-system-x86_64 "install qemu (e.g. 'pacman -S qemu-desktop' / 'apt install qemu-system-x86')"
need_file "$DISK" "run setup.sh first to create it"
need_kvm

OVMF_CODE="$(find_ovmf_code)" || die "OVMF firmware not found - install it (e.g. 'pacman -S edk2-ovmf' / 'apt install ovmf')"
OVMF_VARS_TEMPLATE="$(find_ovmf_vars_template)" || die "OVMF_VARS firmware not found - install it (e.g. 'pacman -S edk2-ovmf' / 'apt install ovmf')"

VARS_FILE="$DISK.vars.fd"
[ -e "$VARS_FILE" ] || cp "$OVMF_VARS_TEMPLATE" "$VARS_FILE"

# virtio-blk/virtio-net for real throughput; setup.sh stages their drivers
# automatically (re-run it on a fresh disk if this hangs/BSODs on boot).
EXTRA_ARGS=()
DISPLAY_ARGS=(-vga std)

if [ "$GPU" = "1" ]; then
  qemu-system-x86_64 -device virtio-vga-gl,help 2>&1 | grep -q '^  venus=' \
    || die "this qemu build's virtio-vga-gl lacks 'venus' support"
  ls /usr/share/vulkan/icd.d/*.json >/dev/null 2>&1 \
    || die "no Vulkan ICD found on host (/usr/share/vulkan/icd.d) - install your GPU's vulkan driver"
  qemu-system-x86_64 -display help 2>&1 | grep -qx gtk \
    || die "qemu was built without the gtk display backend (needed for gl=on rendering)"

  # virtio-vga-gl boots fine with no guest driver at all (Windows just falls
  # back to a generic display), so this is safe to leave on for everyday use.
  # The actual instability the upstream driver project warns about only
  # shows up once that driver is doing real rendering work - see below.
  warn "GPU=1: EXPERIMENTAL acceleration. Windows boots fine without any"
  warn "extra driver (falls back to a generic display), but real GPU use"
  warn "needs an unstable, unofficial Windows Vulkan/venus driver"
  warn "(github.com/arehnman/kvm-guest-drivers-windows) that you build and"
  warn "install yourself inside the guest - it can crash/hang once it's"
  warn "doing real rendering work. You'll also need DXVK"
  warn "(github.com/doitsujin/dxvk) dropped into whatever game/app you want"
  warn "accelerated."
  EXTRA_ARGS+=(-object memory-backend-memfd,id=mem0,size="$RAM",share=on -numa node,memdev=mem0)
  DISPLAY_ARGS=(-vga none -device virtio-vga-gl,hostmem="$HOSTMEM",blob=on,venus=on -display gtk,gl=on)
fi

log "starting $DISK$( [ "$GPU" = "1" ] && echo ' (GPU acceleration enabled)' )"
exec qemu-system-x86_64 \
  -name easywin \
  -machine q35 -accel kvm -cpu host \
  -smp "$SMP" -m "$RAM" \
  "${EXTRA_ARGS[@]}" \
  -drive file="$OVMF_CODE",if=pflash,format=raw,readonly=on \
  -drive file="$VARS_FILE",if=pflash,format=raw \
  -drive file="$DISK",if=virtio,format=qcow2 \
  -device virtio-net-pci,netdev=net0 -netdev user,id=net0 \
  "${DISPLAY_ARGS[@]}" \
  -usb -device usb-tablet
