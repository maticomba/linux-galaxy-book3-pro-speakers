# Capturing the Windows amplifier init with QEMU + VFIO

This is how [`fix/windows-amp-init-940xfg-ka1ar.txt`](../fix/windows-amp-init-940xfg-ka1ar.txt)
was obtained on a Galaxy Book3 Pro 14" (940XFG, NP940XFG-KA1AR) with Ubuntu 24.04
and a dual-boot Windows 11. Use this guide if your SKU differs and you want your
own sequence.

> **Read the whole page before starting.** You will temporarily take the audio
> controller, and the other functions in its IOMMU group, away from the host. The
> procedure was designed to be read-only for the hardware and for the Windows
> disk, but you are on your own.

## 0. Requirements

- VT-d enabled in firmware (the IOMMU must be active: `ls /sys/kernel/iommu_groups/`).
- `qemu-system-x86`, `ovmf`, `qemu-utils`.
- Windows installed on the internal NVMe. BitLocker was off on the reference
  machine. With BitLocker on, expect the VM to ask for the recovery key.
- Enough free RAM for a 4 GiB guest in `/dev/shm`.

## 1. Know your IOMMU group

```bash
for d in /sys/kernel/iommu_groups/*/devices/*; do
  g=${d#*/iommu_groups/}; echo "group ${g%%/*}: $(lspci -nns ${d##*/})"
done | grep -E "1f\.[0-9]"
```

On the 940XFG, the audio controller `00:1f.3` (`8086:51ca`) shares group 16 with
`00:1f.0` (LPC/eSPI), `00:1f.4` (SMBus) and `00:1f.5` (SPI flash). None of them
supports reset (`/sys/bus/pci/devices/0000:00:1f.*/reset_method` absent). VFIO
needs every device in the group either bound to `vfio-pci` or without a driver.
Only `1f.3` is given to the guest; the others just stay unbound.

**Do not hot-unbind `intel-spi`.** `fwupd` keeps `/dev/mtd0` open, and unbinding
left `/proc/mtd` readers stuck in uninterruptible sleep until a reboot. The flash
was not written, but it is not a state you want to be in. Use a one-time boot
instead.

## 2. One-time boot with the drivers blacklisted

At the GRUB menu (hold Shift or press Esc), select Ubuntu, press `e`, append to
the `linux ...` line:

```
modprobe.blacklist=snd_sof_pci_intel_tgl,snd_hda_intel,spi_intel_pci,i2c_i801
```

and boot with Ctrl+X. Nothing is saved; the next normal reboot restores
everything. Host audio, SMBus sensors and SPI flash access are unavailable in
this boot.

Then:

```bash
sudo modprobe vfio-pci ids=8086:51ca
lspci -nnk -s 00:1f.3          # Kernel driver in use: vfio-pci
for a in 1f.0 1f.4 1f.5; do ls -l /sys/bus/pci/devices/0000:00:$a/driver 2>/dev/null || echo "$a: no driver"; done
sudo cp /sys/firmware/acpi/tables/NHLT capture/nhlt.bin
```

The NHLT table describes the audio endpoints. The Windows driver package looks
for it, and QEMU passes it with `-acpitable`.

## 3. Disk overlay and firmware variables

```bash
cd capture
qemu-img create -f qcow2 -F raw -b /dev/nvme0n1 win-overlay.qcow2
cp /usr/share/OVMF/OVMF_VARS_4M.fd OVMF_VARS.fd
```

The overlay receives every write the guest makes. The backing device is only
read. Delete the overlay afterwards.

## 4. Boot and trace

```bash
sudo ./run-win.sh        # first a plain boot, to check Windows comes up
sudo ./capture.sh        # then the traced boot
```

Notes from the first boots:

- If OVMF boots shim/GRUB instead of Windows, enter the EFI shell once and run
  `fs0:\EFI\Microsoft\Boot\bootmgfw.efi` (the right `fsN:` may differ). After
  that, the boot entry is remembered in `OVMF_VARS.fd`.
- `-cpu host,-vmx` is required. With nested VMX exposed and
  `hypervisorlaunchtype Auto`, Windows looped into "Preparing automatic repair".
- No network is given to the guest (`-nic none`). A PIN login will not work
  without the TPM, but you don't need to log in: the driver initializes the amps
  before the lock screen, and **Win+Ctrl+Enter** (Narrator) makes it play sound
  there.
- With tracing (`x-no-mmap=on`) the guest is slower but behaves the same.

While it runs, add marks from another terminal so the logs can be split into
phases:

```bash
echo "lock-screen" > /tmp/hda-mark
echo "narrator-on"  > /tmp/hda-mark
```

Shut Windows down from its UI and the capture ends by itself.

## 5. Output

`captures/<date>/`:

| File | Content |
|---|---|
| `verbs.log` | every CORB verb and RIRB response, timestamped and decoded |
| `coef.log` | indirect COEF accesses on NID `0x20`, plus one `AMP pack:` line per amplifier write (`amp`, `reg`, `0x24`, `0x25`, `cmd`) |
| `regs.log` | controller register writes (CORB/RIRB setup, etc.) |
| `events.log` | marks and start/end |

The fix table is the ordered list of `AMP pack` writes from the driver's init
(reset → base table → filters → protection → DSP on → disable). The enable /
disable writes around each playback (`0x203a`, `0x23ff`) are handled separately
by the script.

## 6. Back to normal

Reboot normally. The blacklist was only for that boot, `vfio-pci` is not
configured persistently, and the overlay can be deleted
(`rm capture/win-overlay.qcow2`). Windows itself never saw a write.

## How the tracer works

- `x-no-mmap=on` makes QEMU handle every MMIO access to the controller's BAR
  instead of mapping it into the guest. The `vfio_region_write` /
  `vfio_region_read` trace events then show each register access.
- `hda-trace.py` keeps a shadow copy of the HDA registers. When the guest bumps
  `CORBWP`, it reads the new CORB entries from guest RAM. When the guest reads
  `RIRBWP`, it reads the new responses. Guest RAM is a `memory-backend-file`
  with `share=on` in `/dev/shm`, so it can be read directly. With
  `max-ram-below-4g=2G` the guest-physical to file offset mapping is trivial.
- QEMU's stderr is piped into the tracer through a 4 KiB pipe
  (`F_SETPIPE_SZ`), so QEMU blocks if it gets more than a few lines ahead. The
  ring buffers are therefore read before the guest can overwrite them.
- Intel controllers have a fixed 256-entry CORB/RIRB, and the driver may never
  write `CORBSIZE`. The tracer assumes 256 and takes the real value from register
  reads.
- COEF writes are tracked with the auto-increment of the COEF index, which
  rebuilds the amplifier packs (`0x22` amp, `0x23` reg, `0x24`/`0x25` value,
  `0x26` command).
