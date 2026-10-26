#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
alc298-amp-init-win.py — initialize the 4 internal speaker amplifiers behind the
Realtek ALC298 of the Samsung Galaxy Book3 Pro 14" (940XFG, codec SSID
0x144dc882) by replaying EXACTLY the sequence the Windows driver sends on this
same machine (captured 2026-10-26): software reset, base register table,
biquad filter coefficients, protection parameters and DSP enable.

Data: windows-amp-init-940xfg-ka1ar.txt (amp reg high low command, in order).

How each amplifier write works (same as Windows):
  1. read COEF 0x26 until bit 4 (busy) is 0
  2. SET_COEF_INDEX 0x23, then 4 x SET_PROC_COEF (the index auto-increments):
     0x23 = register, 0x24 = high word, 0x25 = low word,
     0x26 = command (0xb011 = 16-bit write, 0xb017 = 24-bit coefficient write)
  The target amplifier is selected beforehand with COEF 0x22.
  All of this goes through vendor NID 0x20 of the ALC298.

Usage:
  alc298-amp-init-win.py --dry-run [--log FILE]   does not touch the hardware
  alc298-amp-init-win.py                          initialize and enable
  alc298-amp-init-win.py --off                    only disable the 4 amps
"""
import argparse
import fcntl
import glob
import os
import struct
import sys
import time

NID = 0x20
AMPS = (0x38, 0x39, 0x3C, 0x3D)
VENDOR, SSID = "0x10ec0298", "0x144dc882"

# HDA_IOCTL_VERB_WRITE = _IOWR('H', 0x11, struct hda_verb_ioctl{u32 verb; u32 res;})
HDA_IOCTL_VERB_WRITE = (3 << 30) | (8 << 16) | (ord('H') << 8) | 0x11


class Codec:
    def __init__(self, dry_run, log):
        self.dry = dry_run
        self.log = open(log, "w") if log else None
        self.fd = None
        if not dry_run:
            dev = self.find()
            self.fd = os.open(dev, os.O_RDWR)

    @staticmethod
    def find():
        for d in sorted(glob.glob("/sys/class/sound/hwC*D*")):
            try:
                if (open(d + "/vendor_id").read().strip() == VENDOR
                        and open(d + "/subsystem_id").read().strip() == SSID):
                    return "/dev/snd/" + os.path.basename(d)
            except OSError:
                pass
        print("ALC298 with SSID 0x144dc882 not found; doing nothing", file=sys.stderr)
        sys.exit(0)

    def verb(self, v, fake_resp=0):
        # v is in HDA bus format: [cad:4][nid:7 in bits 27:20][verb+payload:20].
        # The hwdep ioctl expects the hda-verb format instead, with the NID in bits 31:24
        # (kernel: snd_hda_codec_read(codec, v >> 24, 0, (v >> 8) & 0xffff, v & 0xff)).
        # Bug fixed 2026-10-26: the bus format used to be passed through and every
        # COEF write ended up on NID 0x02.
        if self.log:
            self.log.write(f"0x{v:08x}\n")
        if self.dry:
            return fake_resp
        nid = (v >> 20) & 0x7F
        buf = bytearray(struct.pack("II", (nid << 24) | (v & 0xFFFFF), 0))
        fcntl.ioctl(self.fd, HDA_IOCTL_VERB_WRITE, buf)
        return struct.unpack("II", buf)[1]

    # 4-bit verbs on NID 0x20 (codec address 0, as on Windows)
    def set_idx(self, idx):
        self.verb((NID << 20) | (0x5 << 16) | (idx & 0xFFFF))

    def set_coef(self, val):
        self.verb((NID << 20) | (0x4 << 16) | (val & 0xFFFF))

    def self_check(self):
        """Before writing anything: make sure reads really reach the right codec."""
        if self.dry:
            return
        vid = self.verb((0x00 << 20) | (0xF00 << 8) | 0x00)          # GET_PARAM VENDOR_ID (root node)
        if vid != 0x10EC0298:
            sys.exit(f"CHECK FAILED: vendor_id read = 0x{vid:08x}, expected 0x10ec0298. Writing nothing.")
        self.set_idx(0x26)
        st = self.get_coef(fake=0)
        # 0xb0xx = amps already used; 0x30xx = freshly powered (cold boot)
        if st & 0x7F00 != 0x3000:
            sys.exit(f"CHECK FAILED: COEF20[0x26] = 0x{st:04x}, expected 0xb0xx or 0x30xx. Writing nothing.")
        print(f"check OK: vendor 0x{vid:08x}, COEF20[0x26] = 0x{st:04x}")

    def speaker_pin_on(self):
        # Same as the end of Andycodeman's script (and Windows sets PIN_CTL 0x40 before playback):
        # pin 0x17 as output and its output amp unmuted.
        self.verb((0x17 << 20) | (0x707 << 8) | 0x40)   # SET_PIN_WIDGET_CONTROL OUT
        self.verb((0x17 << 20) | (0x3 << 16) | 0xB000)  # SET_AMP_GAIN_MUTE out L+R, unmuted

    def get_coef(self, fake=0xB001):
        return self.verb((NID << 20) | (0xC << 16), fake) & 0xFFFF


class Amps:
    def __init__(self, codec):
        self.c = codec
        self.current = None

    def select(self, amp):
        if amp != self.current:
            self.c.set_idx(0x22)
            self.c.set_coef(amp)
            self.current = amp

    def wait_ready(self, timeout=0.2):
        t0 = time.time()
        while True:
            self.c.set_idx(0x26)
            st = self.c.get_coef()
            if not st & 0x10:
                return
            if time.time() - t0 > timeout:
                raise TimeoutError(f"amp 0x{self.current:02x} busy (0x26=0x{st:04x})")
            time.sleep(0.001)

    def pack(self, amp, reg, hi, lo, cmd):
        self.select(amp)
        self.wait_ready()
        self.c.set_idx(0x23)
        for v in (reg, hi, lo, cmd):
            self.c.set_coef(v)

    def disable_all(self):
        for a in AMPS:
            self.pack(a, 0x23FF, 0, 0x0000, 0xB011)
            self.pack(a, 0x203A, 0, 0x0080, 0xB011)

    def enable_all(self):
        # same order Windows uses when playback starts
        for a in AMPS:
            self.pack(a, 0x203A, 0, 0x0081, 0xB011)
            self.pack(a, 0x23FF, 0, 0x0001, 0xB011)


def load_table(path):
    rows = []
    for line in open(path):
        if not line.startswith("0x"):
            continue
        amp, reg, hi, lo, cmd = (int(x, 16) for x in line.split())
        if amp not in AMPS or cmd not in (0xB011, 0xB017) or not 0x2000 <= reg <= 0x23FF:
            sys.exit(f"invalid line in table: {line.strip()}")
        rows.append((amp, reg, hi, lo, cmd))
    if len(rows) != 398:
        sys.exit(f"table has {len(rows)} rows, expected 398; doing nothing")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "windows-amp-init-940xfg-ka1ar.txt"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log")
    ap.add_argument("--off", action="store_true", help="only disable the amplifiers")
    ap.add_argument("--no-enable", action="store_true", help="initialize but leave them disabled")
    ap.add_argument("--dsp-off", action="store_true",
                    help="diagnostic: same as Windows but leave 0x23e1 (DSP) at 0 at the end")
    ap.add_argument("--enable-only", metavar="AMPS",
                    help="no init: disable all and enable only these (e.g. 3c,3d)")
    a = ap.parse_args()

    if not a.dry_run:
        # Only one process at a time on the codec (boot, resume, manual run).
        lock = open("/run/alc298-amp-win.lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
    codec = Codec(a.dry_run, a.log)
    codec.self_check()
    amps = Amps(codec)
    if a.enable_only:
        sel = [int(x, 16) for x in a.enable_only.split(",")]
        if not sel or any(x not in AMPS for x in sel):
            sys.exit("valid amps: 38,39,3c,3d")
        amps.disable_all()
        for x in sel:
            amps.pack(x, 0x203A, 0, 0x0081, 0xB011)
            amps.pack(x, 0x23FF, 0, 0x0001, 0xB011)
        codec.speaker_pin_on()
        print("enabled only: " + ", ".join(f"0x{x:02x}" for x in sel))
        return
    if a.off:
        amps.disable_all()
        print("amplifiers disabled")
        return
    rows = load_table(a.table)
    if a.dsp_off:
        n = 0
        for i, (amp, reg, hi, lo, cmd) in enumerate(rows):
            if reg == 0x23E1 and lo == 0x0001:
                rows[i] = (amp, reg, hi, 0x0000, cmd); n += 1
        if n != 4:
            sys.exit(f"--dsp-off: expected 4 writes of 0x23e1=1, found {n}; doing nothing")
    try:
        for r in rows:          # the table already starts with reset + disable of each amp
            amps.pack(*r)
        if not a.no_enable:
            amps.enable_all()
            codec.speaker_pin_on()
    except Exception as e:
        print(f"ERROR: {e}. Disabling the amplifiers for safety.", file=sys.stderr)
        try:
            amps.disable_all()
        finally:
            sys.exit(1)
    print(("[dry run] " if a.dry_run else "") +
          f"Windows init applied: {len(rows)} writes" +
          ("" if a.no_enable else ", amplifiers enabled, pin 0x17 active"))


if __name__ == "__main__":
    main()
