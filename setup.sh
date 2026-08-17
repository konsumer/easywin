#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

CACHE_DIR="$SCRIPT_DIR/.cache"
USERNAME="${USERNAME:-User}"
IMAGE_INDEX="${IMAGE_INDEX:-1}"
VIRTIO_WIN_URL="https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"
VIRTIO_ISO="$CACHE_DIR/virtio-win.iso"

usage () { echo "usage: $0 <source.iso> <disk.qcow2> [size]" >&2; exit 1; }
[ $# -ge 2 ] || usage
SRC_ISO="$1"
DISK="$2"
SIZE="${3:-64G}"
RAM="${RAM:-8G}"
SMP="${SMP:-4}"

need_cmd qemu-system-x86_64 "install qemu (e.g. 'pacman -S qemu-desktop' / 'apt install qemu-system-x86')"
need_cmd qemu-img "install qemu-img (e.g. 'pacman -S qemu-img' / 'apt install qemu-utils')"
need_cmd xorriso "install xorriso (e.g. 'pacman -S libisoburn' / 'apt install xorriso')"
need_cmd curl "install curl"
need_cmd python3 "install python3 (used to nudge past prompts and drive WinPE)"
need_file "$SRC_ISO" "pass the path to your Windows 11 installer ISO"
need_kvm

OVMF_CODE="$(find_ovmf_code)" || die "OVMF firmware not found - install it (e.g. 'pacman -S edk2-ovmf' / 'apt install ovmf')"
OVMF_VARS_TEMPLATE="$(find_ovmf_vars_template)" || die "OVMF_VARS firmware not found - install it (e.g. 'pacman -S edk2-ovmf' / 'apt install ovmf')"

[ -e "$DISK" ] && die "$DISK already exists - remove it first if you want to reinstall"

mkdir -p "$CACHE_DIR"
if [ ! -e "$VIRTIO_ISO" ]; then
  log "downloading virtio-win drivers (first run only, cached in $CACHE_DIR)"
  curl -fL --progress-bar -o "$VIRTIO_ISO.part" "$VIRTIO_WIN_URL"
  mv "$VIRTIO_ISO.part" "$VIRTIO_ISO"
fi

WORKDIR="$(mktemp -d "$CACHE_DIR/build.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

sed -e "s/__USERNAME__/$USERNAME/g" -e "s/__IMAGE_INDEX__/$IMAGE_INDEX/g" \
  "$SCRIPT_DIR/autounattend.xml" > "$WORKDIR/autounattend.xml"
AUTOUNATTEND_ISO="$WORKDIR/autounattend.iso"
xorriso -as mkisofs -o "$AUTOUNATTEND_ISO" -V AUTOUNATTEND -J -R "$WORKDIR/autounattend.xml" >/dev/null 2>&1

log "creating $DISK ($SIZE)"
qemu-img create -f qcow2 "$DISK" "$SIZE" >/dev/null

VARS_FILE="$DISK.vars.fd"
cp "$OVMF_VARS_TEMPLATE" "$VARS_FILE"

log "installing unattended (user: $USERNAME, image index: $IMAGE_INDEX) -"
log "this partitions the $SIZE disk, installs Windows, creates a local"
log "account, and shuts down when done - no interaction needed. Set"
log "IMAGE_INDEX=N if your ISO has multiple editions and it picks the"
log "wrong one."
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
#
# Under host load, the disk-boot path can lose the race against the
# install media's own "press any key to boot from CD or DVD" prompt, which
# then just sits waiting for a key nobody will press. nudge_boot_prompt
# sends a few early Enter presses over the monitor to dismiss it.
INSTALL_MONITOR_SOCK="$WORKDIR/install.sock"
nudge_boot_prompt "$INSTALL_MONITOR_SOCK"
qemu-system-x86_64 \
  -name easywin-setup \
  -machine q35 -accel kvm -cpu host \
  -smp "$SMP" -m "$RAM" \
  -drive file="$OVMF_CODE",if=pflash,format=raw,readonly=on \
  -drive file="$VARS_FILE",if=pflash,format=raw \
  -drive file="$DISK",format=qcow2,media=disk \
  -drive if=none,id=src,file="$SRC_ISO",format=raw,readonly=on \
  -device virtio-blk-pci,drive=src,bootindex=1 \
  -drive file="$SRC_ISO",media=cdrom,format=raw,readonly=on \
  -drive file="$AUTOUNATTEND_ISO",media=cdrom,format=raw,readonly=on \
  -device e1000,netdev=net0 -netdev user,id=net0 \
  -monitor unix:"$INSTALL_MONITOR_SOCK",server,nowait \
  -vga std \
  -usb -device usb-tablet

log "install done - staging virtio-blk/virtio-net drivers into $DISK -"
log "this boots WinPE again to run DISM offline driver injection, and"
log "shuts down when done - no interaction needed."
# run.sh boots with virtio-blk/virtio-net for real throughput, which Windows
# doesn't have drivers for out of the box, and which Windows Setup's own
# answer-file engine can't inject mid-install (a windowsPE-pass
# RunSynchronous block errors out immediately if it isn't paired with a full
# ImageInstall). So this re-boots the same install media into WinPE and
# scripts the same Shift+F10 rescue-console DISM commands a person would
# type by hand - see inject-drivers.py. The now-installed target disk has a
# bootable Windows on it, so unlike a blank disk it needs an explicit
# bootindex, or it can unpredictably win boot priority over the install
# media instead of WinPE coming up.
STAGE_MONITOR_SOCK="$WORKDIR/stage.sock"
nudge_boot_prompt "$STAGE_MONITOR_SOCK"
python3 "$SCRIPT_DIR/inject-drivers.py" "$STAGE_MONITOR_SOCK" &
qemu-system-x86_64 \
  -name easywin-stage-drivers \
  -machine q35 -accel kvm -cpu host \
  -smp "$SMP" -m "$RAM" \
  -drive file="$OVMF_CODE",if=pflash,format=raw,readonly=on \
  -drive file="$VARS_FILE",if=pflash,format=raw \
  -drive if=none,id=tgt,file="$DISK",format=qcow2 \
  -device ide-hd,drive=tgt,bootindex=2 \
  -drive if=none,id=src,file="$SRC_ISO",format=raw,readonly=on \
  -device virtio-blk-pci,drive=src,bootindex=1 \
  -drive file="$SRC_ISO",media=cdrom,format=raw,readonly=on \
  -drive file="$VIRTIO_ISO",media=cdrom,format=raw,readonly=on \
  -device e1000,netdev=net0 -netdev user,id=net0 \
  -monitor unix:"$STAGE_MONITOR_SOCK",server,nowait \
  -vga std \
  -usb -device usb-tablet

log "setup finished - your disk is ready: $DISK (run.sh will use it)"
