The purpose of this is easy setup of win11 on linux, in a qemu, without requiring a GUI or any other stuff, like virt-manager. First, go [here](https://www.microsoft.com/en-us/evalcenter/download-windows-11-iot-enterprise-ltsc-eval) and download the LTSC disk for x86-64 (LTSC is already lean - no Store, no Xbox app, no Copilot - so no debloating step is needed).

It's just 2 python scripts (`setup.py` and `run.py`) with no dependencies outside the standard library, so they're easy to read and change for your own purposes. You need `qemu`, `qemu-img`, `xorriso`, and OVMF (`edk2-ovmf` / `ovmf`) installed on the host.

## setup windows

Create & install your OS on a hard drive image:

```sh
# Install your OS on a hard drive image
./setup.py win11-ltsc.iso win11.qcow2
```

This creates & installs onto `win11.qcow2` fully unattended (via `autounattend.xml`): it partitions the disk, installs Windows, skips the TPM/CPU/RAM hardware checks, creates a local account called `User` (no Microsoft/work account prompt), and shuts down when done.

Options (`./setup.py --help`): a disk size as a third argument (default `64G`), `--user yourname`, `--image-index N` if your ISO has multiple editions (default `1`), plus `--ram`/`--smp` for the installer VM.

Once Windows is installed, the script automatically reboots into WinPE a second time and uses DISM to inject virtio-blk/virtio-net drivers into the offline image, so `run.py` gets real disk/network throughput without a separate step. No interaction needed for either phase.

## run your disk

```sh
./run.py win11.qcow2
```

Everyday use: virtio-blk/virtio-net (staged by `setup.py` above), plain framebuffer display, no GPU acceleration. `--ram 16G` and `--smp 8` change what the VM gets.

## run with GPU acceleration (experimental)

```sh
./run.py --gpu win11.qcow2
```

This swaps the display for virtio-gpu with [venus](https://docs.mesa3d.org/drivers/venus.html) (Vulkan passthrough to the host GPU, no PCI passthrough needed). It boots fine with no extra guest driver (Windows just falls back to a generic display), so it's safe to leave on. Real acceleration needs an **unofficial, unstable, build-it-yourself** Windows driver ([arehnman/kvm-guest-drivers-windows](https://github.com/arehnman/kvm-guest-drivers-windows), forked from an abandoned upstream PR) - its own README says it can crash or hang the guest once it's doing real rendering work. There is no installer for it; you build and test-sign it yourself inside the guest. Once that driver is in and [DXVK](https://github.com/doitsujin/dxvk) is dropped into a game/app's folder, it should use the host GPU. Leave `--gpu` off for anything that needs to be reliable.
