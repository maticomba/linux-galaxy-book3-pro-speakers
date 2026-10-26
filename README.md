# Internal speakers on the Samsung Galaxy Book3 Pro 14" (940XFG) under Linux

On the Samsung Galaxy Book3 Pro 14" the internal speakers are silent under Linux,
while headphones and the microphone work. This repository contains a working fix
that **replays, byte for byte, the amplifier initialization the Windows driver
performs on this exact machine**. It was obtained by tracing the Windows driver
from a VM with the audio controller passed through. It also contains the tools,
the raw capture and the full write-up of how it was done.

> **Status (2026-10-26): working.** Both channels, all four amplifiers, with the
> Windows crossover/EQ filters loaded. The fix applies itself at boot (udev +
> systemd) and after resume from suspend. Verified on one machine only (see
> [Tested hardware](#tested-hardware)).
>
> **Tested on Ubuntu 24.04 (kernel 6.8).** Nothing in it is Ubuntu-specific: it
> should work on any systemd-based distribution (see
> [Other distributions](#other-distributions)). Reports from other distros and
> other Galaxy Book3 SKUs are welcome.

- [TL;DR / install](#tldr--install)
- [Tested hardware](#tested-hardware)
- [Other distributions](#other-distributions)
- [Background: why the speakers are silent](#background-why-the-speakers-are-silent)
- [What Windows actually does](#what-windows-actually-does)
- [How the fix works](#how-the-fix-works)
- [How the Windows sequence was captured](#how-the-windows-sequence-was-captured)
- [Validation](#validation)
- [Pitfalls found along the way](#pitfalls-found-along-the-way)
- [Repository layout](#repository-layout)
- [Safety notes](#safety-notes)
- [Sources and credits](#sources-and-credits)

---

## TL;DR / install

**Read [Safety notes](#safety-notes) first.** This writes vendor registers of the
speaker amplifiers. The script refuses to run on anything that is not a 940XFG
with the expected codec, and every value it writes is one Windows writes on the
same hardware. Still, use it at your own risk.

```bash
git clone https://github.com/maticomba/linux-galaxy-book3-pro-speakers.git && cd linux-galaxy-book3-pro-speakers

# 1. Try it once without installing anything (start with the volume at 20-30 %)
sudo python3 -I fix/alc298-amp-init-win.py
#   check OK: vendor 0x10ec0298, COEF20[0x26] = 0x....
#   Windows init applied: 398 writes, amplifiers enabled, pin 0x17 active

# Disable the amplifiers again at any time
sudo python3 -I fix/alc298-amp-init-win.py --off

# 2. Optional: interactive tone test (tweeters / woofers / crossover)
sudo ./tests/speaker-test.sh

# 3. Install (applies at boot and after resume)
cd fix && sudo ./install.sh

# Uninstall
sudo ./uninstall.sh
```

If you had [Andycodeman's `speaker-fix-940xfg`][andy-940xfg] installed,
uninstall it first (`install.sh` checks for this and stops).

Without installing, the configuration is lost on suspend or reboot; run step 1
again.

**Temporarily disable the installed fix without uninstalling:** move
`/etc/systemd/system/alc298-amp-win.service` and
`/etc/udev/rules.d/99-alc298-amp-win.rules` somewhere else, run
`chmod -x /lib/systemd/system-sleep/alc298-amp-win`, then
`systemctl daemon-reload && udevadm control --reload-rules`.
(`systemctl mask` does not work because the unit lives in `/etc/systemd/system`.)

## Tested hardware

| | |
|---|---|
| Model | Samsung Galaxy Book3 Pro 14" — DMI product `940XFG`, board `NP940XFG-KA1AR` |
| BIOS | `P03RGS.150.230330.ZW` |
| Codec | Realtek ALC298, vendor `0x10ec0298`, subsystem `0x144dc882` |
| Audio controller | Intel `8086:51ca` (PCI `00:1f.3`), SOF driver (`sof-audio-pci-intel-tgl`, card `sof-hda-dsp`) |
| OS | Ubuntu 24.04.5, kernel `6.8.0-147-generic`, Secure Boot on |
| Windows reference | Realtek driver `6.0.9689.1` (Samsung package `HDXSSTSamsung`, `RTKVHD64.sys`) |

The safety check requires codec SSID `0x144dc882`; `install.sh` also requires
DMI product `940XFG`. Other Galaxy Book3 variants (e.g. 16" / 960XFG, other
regional SKUs) **may** use different amplifier parameters. The table here is the
one Windows sends on the KA1AR board. If you have a different SKU, the right way
is to capture your own sequence (see [capture](#how-the-windows-sequence-was-captured)).

## Other distributions

The fix only relies on things every mainstream distribution has:

- the kernel's HDA/SOF audio driver (the same code everywhere);
- the ALSA hwdep device of the codec, `/dev/snd/hwC*D*`, which needs
  `CONFIG_SND_HDA_HWDEP=y` (enabled in Ubuntu, Debian, Fedora and Arch kernels);
- `python3` (standard library only), `udev` and `systemd`.

Quick check before trying it:

```bash
ls /dev/snd/hwC*D*                                   # hwdep nodes exist
grep -l 0x144dc882 /sys/class/sound/hwC*D*/subsystem_id   # the ALC298 with this SSID
cat /sys/class/dmi/id/product_name                   # 940XFG (required by install.sh)
```

`install.sh` puts the resume hook in `/lib/systemd/system-sleep/`. On distributions
where `/lib` is not a symlink to `/usr/lib`, move it to `/usr/lib/systemd/system-sleep/`.
Without systemd, run `fix/alc298-amp-init-win.py` from your init system once the
sound card is up and again after every resume.

## Background: why the speakers are silent

The ALC298 does not drive the speakers directly. Behind it there are **four
smart amplifiers** (two main/woofer amps at addresses `0x38`/`0x39`, two
tweeter amps at `0x3c`/`0x3d`). The codec reaches them through an indirect
register window on its vendor widget, **NID `0x20`**, using processing
coefficients (COEF):

| COEF index | Meaning |
|---|---|
| `0x22` | amplifier select (`0x38`, `0x39`, `0x3c`, `0x3d`) |
| `0x23` | amplifier register (`0x2000`–`0x23ff`) |
| `0x24` | value, high word |
| `0x25` | value, low word |
| `0x26` | command: `0xb011` = 16-bit write, `0xb017` = 24-bit coefficient write; bit 4 = busy |

The COEF index auto-increments, so one amplifier write is
`SET_COEF_INDEX 0x23` followed by four `SET_PROC_COEF` (reg, hi, lo, cmd), after
polling `0x26` until the busy bit clears.

Until something initializes and enables those amplifiers, nothing comes out of
the speakers even though the codec, the DAC and the pin are working. That is why
headphones (driven by the codec itself) work and the speakers don't.

Upstream Linux initializes these Samsung amplifiers for other models in
`sound/pci/hda/patch_realtek.c`. The first sequence (2022) was "extracted from
Windows driver"; the Galaxy Book3 Pro 360 support (2024) was "captured using a
modified version of QEMU". Today it lives in the `alc298_samsung_v2_*` helpers
with tables for 2 and 4 amps (`V2_2`/`V2_4`). The kernel used here (6.8) has no
quirk for this machine's SSID `0x144dc882`.
[Andycodeman's speaker-fix-940xfg][andy-940xfg] fills the gap from user space
with `hda-verb`, using the upstream `V2_4` table plus a write derived from
`RtHDDump` snapshots in [issue #44][issue-44].

## What Windows actually does

Tracing the Windows driver on the same machine (see below) showed that it does
**considerably more** than the upstream `V2_4` sequence. Compared with Linux
(upstream `V2_4` / Andycodeman's script), Windows:

1. **Resets each amplifier** in software first (`0x2000 = 1`). Linux does not.
2. Writes the same base register table as `V2_4`, **except `0x23ba` on the main
   amps: `0x0098` instead of `0x0094`.**
3. **Loads biquad filter coefficients**: 95 on each main amp and 40 on each
   tweeter, in registers `0x2100`–`0x22dc`, with command `0xb017` (24-bit values).
   Linux loads none. Decoded as Q4.20 little-endian biquads (denominator
   `1 + a1·z⁻¹ + a2·z⁻²`) they are stable and make sense:
   - tweeters: two high-pass stages in cascade (≈ −16 dB at 800 Hz, −5.5 dB at
     1.6 kHz, flat from 3.2 kHz) → a crossover;
   - main amps: a 100–200 Hz high-pass plus equalizer sections.
4. Writes **protection/DSM parameters** `0x238e`–`0x239b`, `0x23a6`, `0x23a7`,
   `0x23b9`, `0x23e0`.
5. Ends with **`0x23e1 = 1`** (protection DSP on). Linux leaves it at 0.
6. **Never writes `0x239e`.** The "SKU-specific `0x239e` write" in the
   user-space fix comes from a register that Windows only **reads**. Snapshot
   tools cannot tell a read from a write. The trace can.
7. Enable/disable around playback is **identical** to Linux:
   `0x203a = 0x81/0x80`, `0x23ff = 1/0`. Windows enables the amps *before* the
   stream starts.
8. Sets GPIO1 as output = 0 and pin `0x17` `PIN_CTL = 0x40`.

The coefficient values are not computed at runtime: they appear verbatim, in
order, as tables inside `RTKVHD64.sys`. The 95 main-amp pairs are identical to one
driver table, and the 40 tweeter pairs match the start of another. The driver
holds 64 such tables. The INF only maps `SUBSYS_144DC882` to a generic model, so
the table selection happens inside the driver binary.

The full sequence (398 amplifier writes: 128 × `0xb011` + 270 × `0xb017`,
ending with the disable of all four amps that Windows does right after init) is
in [`fix/windows-amp-init-940xfg-ka1ar.txt`](fix/windows-amp-init-940xfg-ka1ar.txt).

## How the fix works

[`fix/alc298-amp-init-win.py`](fix/alc298-amp-init-win.py) talks to the codec
through the ALSA hwdep device (`/dev/snd/hwC*D*`, found by vendor + subsystem ID)
with the `HDA_IOCTL_VERB_WRITE` ioctl. It needs no `hda-verb` and no kernel
module.

1. **Safety check before any write.** It reads the codec vendor ID (must be
   `0x10ec0298`) and `COEF20[0x26]`, whose upper bits must look like an amp
   status word: `0xb0xx` after use, `0x30xx` right after a cold boot. Anything
   else aborts without writing.
2. Replays the 398 writes of the table **in Windows order**, with the same busy
   polling (200 ms timeout per write).
3. Enables the four amps in the Windows order (`0x203a = 0x81`, then
   `0x23ff = 1`) and sets pin `0x17` to output, unmuted.
4. On any error it disables all four amps and exits 1. A `flock` on
   `/run/alc298-amp-win.lock` prevents concurrent runs (boot vs. resume vs. manual).

Options: `--dry-run [--log FILE]` (prints every verb, touches nothing),
`--no-enable`, `--off`, `--enable-only 38,39,3c,3d`, and `--dsp-off` (diagnostic:
leave `0x23e1 = 0`).

**Installation** (`fix/install.sh`) adds:

| File | Purpose |
|---|---|
| `/usr/local/sbin/alc298-amp-init-win.py` + `/usr/local/share/alc298-amp-win/<table>` | script and data |
| `/etc/systemd/system/alc298-amp-win.service` | `Type=oneshot`, **no** `RemainAfterExit`, **no** `[Install]` |
| `/etc/udev/rules.d/99-alc298-amp-win.rules` | starts the service when the internal card's `controlC*` appears, matched by **PCI parent `8086:51ca`**, not card number |
| `/lib/systemd/system-sleep/alc298-amp-win` | re-applies after resume (output to the journal via `logger`) |

The amplifiers keep their configuration across the codec's runtime suspend.
After a system suspend they need it again, and the sleep hook handles that.

## How the Windows sequence was captured

The idea follows earlier work, such as [QemuHDADump][qemuhdadump] and the
modified-QEMU capture behind the upstream Galaxy Book3 Pro 360 patch: run the
**real Windows install** of the laptop inside QEMU with the HDA controller
passed through via VFIO, and log every verb the driver puts in the CORB.

Full step-by-step instructions are in
[`docs/capture-howto.md`](docs/capture-howto.md). In short:

- The audio controller `00:1f.3` shares IOMMU group 16 with LPC (`1f.0`), SMBus
  (`1f.4`) and SPI flash (`1f.5`). None supports reset. They were kept away from
  their drivers with a **one-time GRUB boot** (`modprobe.blacklist=...`) instead
  of hot-unbinding (hot-unbinding `intel-spi` hung the MTD layer; see pitfalls).
- QEMU boots the dual-boot Windows from a **qcow2 overlay** on top of the NVMe,
  with the backing device opened read-only, so Windows never writes the real disk.
  It also gets the laptop's NHLT ACPI table (`-acpitable`) and Samsung SMBIOS
  strings, so the Realtek/Samsung driver package recognises the machine.
  `-cpu host,-vmx` is required (otherwise Windows, with `hypervisorlaunchtype Auto`,
  loops into automatic repair).
- `x-no-mmap=on` on the VFIO device sends every BAR access through QEMU, and the
  `vfio_region_write/read` trace events record it. The guest RAM is a
  `memory-backend-file` in `/dev/shm`. [`capture/hda-trace.py`](capture/hda-trace.py)
  follows `CORBWP`/`RIRBWP`, reads the ring buffers straight from that file and
  decodes the verbs, including the indirect amplifier writes. A 4 KiB pipe keeps
  QEMU in lockstep with the reader.
- No login is needed: the driver initializes the amps at the lock screen, and
  the Narrator (Win+Ctrl+Enter) produces sound there.

The raw capture (5471 verbs, boot → lock screen → Narrator on/off → shutdown) is
in [`capture/data/windows-2026-10-26/`](capture/data/windows-2026-10-26/).
The tools in [`tools/`](tools/) then located the captured coefficients inside
`RTKVHD64.sys`.

## Validation

- **Dry run = capture:** in `--dry-run` the script emits the same amplifier
  writes, in the same order, as Windows.
- **Filters:** decoded and checked for stability (see above). The base table
  matches the kernel's `V2_4` table except `0x23ba`.
- **Driver binary:** the coefficients are present verbatim in `RTKVHD64.sys`.
- **Listening tests** (`tests/speaker-test.sh`, Master −20 dB):

  | Test | Result |
  |---|---|
  | all 4 amps, 1 kHz | both channels |
  | tweeters only, 4 kHz | both channels |
  | tweeters only, 200 Hz | **silent** → the crossover high-pass is active |
  | woofers only, 200 Hz | both channels, quiet |
  | all 4, pink noise | both channels |

- **Readback** on real hardware after applying: `COEF20[0x22] = 0x3d`,
  `COEF20[0x26] = 0xb001`, pin `0x17 PIN_CTL = 0x40`.
- **Lifecycle:** manual run on a cold boot, service started manually, reboot
  (applied ~7.8 s into boot) and suspend/resume (re-applied in < 1 s). All OK.

## Pitfalls found along the way

These cost time and are worth knowing if you write something similar.

- **hwdep ioctl verb format.** `HDA_IOCTL_VERB_WRITE` does *not* take the HDA bus
  verb format (NID in bits 27:20). It takes the `hda-verb` format, with the NID in
  bits 31:24: the kernel does
  `snd_hda_codec_read(codec, v >> 24, 0, (v >> 8) & 0xffff, v & 0xff)`. An early
  version of the script passed bus-format verbs. All COEF writes went to NID
  `0x02` (a DAC with no processing widget, which ignored them), reads came back
  0, and the busy poll "passed". Tests in that state looked meaningful because
  the previous configuration was still loaded in the amps. Hence the read-back
  **self-check** before any write.
- **Cold-boot status word.** Right after power-on `COEF20[0x26]` reads `0x3000`,
  not `0xb0xx`. A too-strict self-check silently refused to run at boot.
- **Card numbering.** With a USB webcam/audio device attached, the internal card
  became `card2`. A udev rule keyed on "the first controlC" fired before the
  codec existed, and `RemainAfterExit=yes` then blocked any retry. The rule now
  matches the PCI parent (`ATTRS{vendor}=="0x8086", ATTRS{device}=="0x51ca"`), and
  the unit has no `RemainAfterExit`.
- **"Device or resource busy" when testing.** An open GNOME Settings → Sound
  panel keeps the PCM open. PipeWire maps the desktop volume onto the ALSA
  `Speaker` control.
- **VFIO capture:**
  - Do not hot-unbind `intel-spi` while `fwupd` holds `/dev/mtd0`: readers end
    up stuck in D state and you need a reboot. The BIOS flash is not touched,
    but use the one-time GRUB blacklist instead.
  - QEMU under `sudo` needs `DISPLAY`/`XAUTHORITY` (the `mutter-Xwaylandauth`
    file) and `GDK_BACKEND=x11` to open its window.
  - OVMF may fall back to shim/GRUB. Boot `\EFI\Microsoft\Boot\bootmgfw.efi`
    from the EFI shell once.
  - QEMU's default `CORBSIZE` reading must not be trusted blindly. Intel
    controllers use a fixed 256 entries; the tracer takes the size from register
    reads.
  - A QMP `pmemsave`-based design deadlocks (the trace pipe fills while waiting
    on QMP). Hence the shared RAM file.

An unrelated monitor problem during testing (external HDMI monitor flickering at
the login screen) turned out to be a bad cable. The script only writes to the
ALC298 codec; HDMI audio is a different codec (`0x8086281f`) and video is i915.

## Repository layout

```
fix/                     the fix (this is all you need)
  alc298-amp-init-win.py   replays the Windows sequence through the ALSA hwdep ioctl
  windows-amp-init-940xfg-ka1ar.txt   398 captured amplifier writes, in order
  alc298-amp-win.service   oneshot unit (started by udev)
  99-alc298-amp-win.rules  udev rule matching the internal card by PCI ID
  alc298-amp-win-sleep     systemd-sleep hook (re-apply after resume)
  install.sh / uninstall.sh
tests/speaker-test.sh    interactive tweeter/woofer/crossover test
capture/                 how the Windows sequence was obtained
  run-win.sh               boots the dual-boot Windows in QEMU with 00:1f.3 via VFIO
  capture.sh               same, with HDA verb tracing
  hda-trace.py             CORB/RIRB tracer (QEMU trace + shared guest RAM)
  data/windows-2026-10-26/ raw capture (xz): verbs, COEF accesses, registers, marks
tools/                   analysis helpers (read-only)
  find-table.py            search files for the captured coefficients
  match-tables.py          match driver tables against the capture
  inspect-table.py         hex context around known values
  diag.sh                  basic audio diagnostic of a machine
docs/
  capture-howto.md         step-by-step VFIO capture guide
```

## Safety notes

- The amplifiers are protected parts with their own DSP. The fix writes **only**
  values the Windows driver writes on the same hardware, in the same order. That
  includes the protection parameters and the protection DSP enable, which the
  upstream `V2_4` sequence leaves out.
- Every run first disables and resets each amp (that is how the Windows sequence
  starts), and any error disables all four.
- The capture runs Windows in a VM against an overlay. The physical disk is
  opened read-only. Do not pass the SPI/LPC/SMBus functions to the guest, and
  read [`docs/capture-howto.md`](docs/capture-howto.md) carefully before trying it.
- This is an unofficial workaround, not affiliated with Samsung, Realtek or
  Intel. Prefer a proper kernel quirk once one exists for your SSID.

## Sources and credits

- **Andycodeman/samsung-galaxy-book-linux-fixes**, `speaker-fix-940xfg` and the
  diagnostic thread in **[issue #44][issue-44]** (V2_2/V2_4 tests, RtHDDump
  snapshots, COEF sequence; Windows-side testing by @derwismtz). Starting point
  and reference for this work. Related: [issue #106][issue-106]
  ([reply][issue-106-reply]). <https://github.com/Andycodeman/samsung-galaxy-book-linux-fixes>
- **Linux kernel**, `sound/pci/hda/patch_realtek.c` (Samsung amp `V2` support:
  `alc298_samsung_v2_init_amps()`, `alc298_samsung_v2_enable_amps()`/`_disable_amps()`
  and the `V2_4` table) and `sound/pci/hda/hda_hwdep.c` / `include/uapi/sound/hda_hwdep.h`
  (`HDA_IOCTL_VERB_WRITE`). <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git>
- **Kacper Michajłow**, upstream commit `a2d57ebec1e1` "ALSA: hda/realtek: Add speaker
  AMP init for Samsung laptops with ALC298" (2022; sequence "extracted from Windows
  driver"), with its reports [kernel bugzilla 207423][bz-207423] and
  [Launchpad 1851518][lp-1851518]. [Commit mirror][kasper-commit].
- **Nick Weihs**, "ALSA: hda/realtek: Implement sound init sequence for Samsung Galaxy
  Book3 Pro 360" (2024; data "captured using a modified version of QEMU"), and
  Takashi Iwai's review. [LKML thread][weihs-lkml].
- **joshuagrisham/samsung-galaxybook-extras**, Samsung Galaxy Book Linux work,
  including a parallel report for the same SSID ([issue #97][grisham-97]).
- **abridgewater/QemuHDADump**, prior art for dumping HDA verbs from a Windows
  guest under QEMU. <https://github.com/abridgewater/QemuHDADump>
- **thesofproject/linux PR #5616**: MAX98390 HDA side-codec driver used by
  Andycodeman's fix for *other* Galaxy Book models with MAX98390 amplifiers. Useful
  context for why this model is different.
  <https://github.com/thesofproject/linux/pull/5616>
- **Intel High Definition Audio Specification** rev. 1.0a: CORB/RIRB, verb format,
  `SET_COEF_INDEX`/`SET_PROC_COEF`.
- **QEMU** documentation for VFIO (`x-no-mmap`), tracing (`-trace enable=...`) and
  `memory-backend-file`. <https://www.qemu.org/docs/master/>
- **alsa-tools** `hda-verb` (verb format used by the hwdep ioctl).
- Windows driver: Realtek HD Audio `6.0.9689.1`, Samsung package
  `HDXSSTSamsung` (`RTKVHD64.sys`). The coefficient values in the table were
  captured from this driver's traffic on the author's own machine. No driver
  files are included in this repository.

[andy-940xfg]: https://github.com/Andycodeman/samsung-galaxy-book-linux-fixes/tree/main/speaker-fix-940xfg
[issue-44]: https://github.com/Andycodeman/samsung-galaxy-book-linux-fixes/issues/44
[issue-106]: https://github.com/Andycodeman/samsung-galaxy-book-linux-fixes/issues/106
[issue-106-reply]: https://github.com/Andycodeman/samsung-galaxy-book-linux-fixes/issues/106#issuecomment-6020627289
[grisham-97]: https://github.com/joshuagrisham/samsung-galaxybook-extras/issues/97
[qemuhdadump]: https://github.com/abridgewater/QemuHDADump
[bz-207423]: https://bugzilla.kernel.org/show_bug.cgi?id=207423
[lp-1851518]: https://bugs.launchpad.net/ubuntu/+source/linux/+bug/1851518
[kasper-commit]: https://ayakael.net/forge/linux-uconsole/commit/7565c1503074
[weihs-lkml]: https://lkml.rescloud.iu.edu/2407.2/05669.html

## License

[GPL-2.0-or-later](LICENSE), the same license as the Linux kernel and alsa-tools,
so the code and the captured sequence can be reused upstream. The register values in
`fix/windows-amp-init-940xfg-ka1ar.txt` are hardware initialization data observed on
the bus while the Windows driver ran on the author's machine; no part of the driver
itself is distributed.
