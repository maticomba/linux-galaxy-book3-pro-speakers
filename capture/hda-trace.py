#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
hda-trace.py — log the HDA verbs a guest sends to a controller passed through
with VFIO, by reading QEMU's trace (vfio_region_write/read) and the guest
memory straight from the shared RAM file.

READ ONLY: writes nothing to the hardware or to the guest.

Usage (QEMU must run with):
  -device vfio-pci,host=...,x-no-mmap=on,x-intx-mmap-timeout-ms=0
  -trace enable=vfio_region_write -trace enable=vfio_region_read
  -object memory-backend-file,id=ram0,size=4G,mem-path=RAMFILE,share=on
  -machine q35,memory-backend=ram0,max-ram-below-4g=2G
and its stderr piped into this script:
  qemu ... 2>&1 >/dev/null | python3 hda-trace.py --ram RAMFILE --out DIR

Outputs in DIR:
  verbs.log   one line per verb (CORB) and per response (RIRB), timestamped
  coef.log    indirect writes/reads to the amplifiers
              (NID 0x20, COEF 0x22/0x23/0x25/0x26), already decoded
  regs.log    writes to relevant controller registers (region0)
  events.log  user marks (see --marks) and notices
"""
import argparse
import os
import re
import sys
import time

WRITE_RE = re.compile(
    r"vfio_region_write\s*\(\s*[0-9a-fA-F:.]+:region(\d+)\+0x([0-9a-fA-F]+),\s*0x([0-9a-fA-F]+),\s*(\d+)\s*\)")
READ_RE = re.compile(
    r"vfio_region_read\s*\(\s*[0-9a-fA-F:.]+:region(\d+)\+0x([0-9a-fA-F]+),\s*(\d+)\s*\)\s*=\s*0x([0-9a-fA-F]+)")

# HDA registers (region0)
CORBLBASE, CORBUBASE, CORBWP, CORBRP, CORBCTL, CORBSIZE = 0x40, 0x44, 0x48, 0x4A, 0x4C, 0x4E
RIRBLBASE, RIRBUBASE, RIRBWP, RINTCNT, RIRBCTL, RIRBSIZE = 0x50, 0x54, 0x58, 0x5A, 0x5C, 0x5E
SIZE_VALUES = {0: 2, 1: 16, 2: 256}

VERB12_NAMES = {
    0xF00: "GET_PARAM", 0xF01: "GET_CONN_SEL", 0x701: "SET_CONN_SEL",
    0xF02: "GET_CONN_LIST", 0xF03: "GET_PROC_STATE", 0x703: "SET_PROC_STATE",
    0xF05: "GET_POWER_STATE", 0x705: "SET_POWER_STATE",
    0xF06: "GET_CONV", 0x706: "SET_CONV", 0xF07: "GET_PIN_CTL", 0x707: "SET_PIN_CTL",
    0xF08: "GET_UNSOL", 0x708: "SET_UNSOL", 0xF09: "GET_PIN_SENSE", 0x709: "EXEC_PIN_SENSE",
    0xF0C: "GET_EAPD_BTL", 0x70C: "SET_EAPD_BTL", 0xF0D: "GET_DIGI_CONV",
    0x70D: "SET_DIGI_CONV_1", 0x70E: "SET_DIGI_CONV_2", 0xF15: "GET_GPIO_DATA", 0x715: "SET_GPIO_DATA",
    0xF16: "GET_GPIO_MASK", 0x716: "SET_GPIO_MASK", 0xF17: "GET_GPIO_DIR", 0x717: "SET_GPIO_DIR",
    0xF1C: "GET_CONFIG_DEFAULT", 0x71C: "SET_CONFIG_DEF_0", 0x71D: "SET_CONFIG_DEF_1",
    0x71E: "SET_CONFIG_DEF_2", 0x71F: "SET_CONFIG_DEF_3", 0xF20: "GET_SUBSYSTEM_ID",
    0x7FF: "RESET", 0xF2D: "GET_CVT_CHAN_CNT", 0x72D: "SET_CVT_CHAN_CNT",
}
VERB4_NAMES = {0x2: "SET_STREAM_FORMAT", 0xA: "GET_STREAM_FORMAT",
               0x3: "SET_AMP_GAIN_MUTE", 0xB: "GET_AMP_GAIN_MUTE",
               0x4: "SET_PROC_COEF", 0xC: "GET_PROC_COEF",
               0x5: "SET_COEF_INDEX", 0xD: "GET_COEF_INDEX"}


def decode(verb):
    cad = verb >> 28
    nid = (verb >> 20) & 0x7F
    v12 = (verb >> 8) & 0xFFF
    v4 = (verb >> 16) & 0xF
    if v4 in VERB4_NAMES and v12 not in VERB12_NAMES:
        return cad, nid, VERB4_NAMES[v4], v4, verb & 0xFFFF
    return cad, nid, VERB12_NAMES.get(v12, f"VERB_{v12:03X}"), v12, verb & 0xFF


class GuestRAM:
    """Read guest RAM from the memory-backend-file file (share=on).
    q35 layout with max-ram-below-4g=LOW: [0, LOW) -> same offset;
    [4 GiB, ...) -> offset LOW + (gpa - 4 GiB)."""
    def __init__(self, path, low):
        import mmap
        deadline = time.time() + 60
        while not os.path.exists(path) or os.path.getsize(path) == 0:
            if time.time() > deadline:
                raise RuntimeError(f"{path} does not show up")
            time.sleep(0.2)
        self.fd = os.open(path, os.O_RDONLY)
        self.size = os.fstat(self.fd).st_size
        self.mm = mmap.mmap(self.fd, self.size, prot=mmap.PROT_READ)
        self.low = low

    def read(self, gpa, size):
        if gpa < self.low:
            off = gpa
        elif gpa >= 1 << 32:
            off = self.low + (gpa - (1 << 32))
        else:
            raise ValueError(f"address 0x{gpa:x} is in the PCI hole")
        if off + size > self.size:
            raise ValueError(f"address 0x{gpa:x} is outside RAM")
        return self.mm[off:off + size]


class Tracer:
    def __init__(self, ram, out):
        self.ram = ram
        self.t0 = time.time()
        self.verbs = open(os.path.join(out, "verbs.log"), "a", buffering=1)
        self.coef = open(os.path.join(out, "coef.log"), "a", buffering=1)
        self.regs = open(os.path.join(out, "regs.log"), "a", buffering=1)
        self.events = open(os.path.join(out, "events.log"), "a", buffering=1)
        self.shadow = bytearray(0x80)
        self.size_known = {}
        self.corb_rp = 0          # last index processed
        self.rirb_rp = 0
        self.rirb_rst_seen = False
        self.pending = []         # verbs sent, waiting for a response (FIFO)
        self.coef_idx = {}        # nid -> current COEF index
        self.coef_shadow = {}     # (nid, idx) -> last value written
        self.nverbs = 0
        self.log_event("trace start")

    # --- helpers ---
    def ts(self):
        return f"{time.time() - self.t0:10.4f}"

    def log_event(self, text):
        self.events.write(f"{self.ts()} {text}\n")
        self.verbs.write(f"{self.ts()} ===== {text}\n")
        self.coef.write(f"{self.ts()} ===== {text}\n")

    def reg(self, off, width):
        return int.from_bytes(self.shadow[off:off + width], "little")

    def size(self, off):
        v = self.shadow[off] & 3
        if v == 0 and not self.size_known.get(off):
            return 256  # Intel: fixed size of 256 entries, the driver does not write it
        return SIZE_VALUES.get(v)

    def base(self, lo, hi):
        return (self.reg(hi, 4) << 32) | (self.reg(lo, 4) & ~0x7F)

    # --- trace events ---
    def on_write(self, off, data, width):
        if off + width > len(self.shadow):
            return
        old = bytes(self.shadow)
        self.shadow[off:off + width] = data.to_bytes(width, "little")
        touched = set(range(off, off + width))
        for so in (CORBSIZE, RIRBSIZE):
            if so in touched:
                self.size_known[so] = True
        if touched & set(range(0x40, 0x60)) or off < 0x10:
            self.regs.write(f"{self.ts()} W +0x{off:02x} = 0x{data:0{width*2}x} ({width})\n")
        # CORB read pointer reset (bit 15 of CORBRP)
        if touched & {CORBRP, CORBRP + 1}:
            if old[CORBRP + 1] & 0x80 and not self.shadow[CORBRP + 1] & 0x80:
                self.corb_rp = 0
        # RIRB write pointer reset (bit 15 of RIRBWP)
        if touched & {RIRBWP + 1} and self.shadow[RIRBWP + 1] & 0x80:
            self.rirb_rp = 0
            self.pending.clear()
            self.shadow[RIRBWP + 1] &= 0x7F
        if touched & {CORBWP} or touched & {CORBCTL}:
            self.corb_update()

    def on_read(self, off, width, value):
        # the size registers are fixed by hardware: take them from reads
        for so in (CORBSIZE, RIRBSIZE):
            if off <= so < off + width:
                self.shadow[so] = (value >> (8 * (so - off))) & 0xFF
                self.size_known[so] = True
        if off <= RIRBWP < off + width:
            wp = (value >> (8 * (RIRBWP - off))) & 0xFF
            self.rirb_update(wp)

    # --- CORB / RIRB ---
    def corb_update(self):
        if not self.shadow[CORBCTL] & 0x02:
            return
        n = self.size(CORBSIZE)
        if not n:
            return
        wp = self.shadow[CORBWP] % n
        if wp == self.corb_rp:
            return
        base = self.base(CORBLBASE, CORBUBASE)
        mem = self.ram.read(base, n * 4)
        i = self.corb_rp
        while i != wp:
            i = (i + 1) % n
            verb = int.from_bytes(mem[i * 4:i * 4 + 4], "little")
            self.emit_verb(verb)
        self.corb_rp = wp

    def rirb_update(self, wp):
        n = self.size(RIRBSIZE)
        if not n or not self.shadow[RIRBCTL] & 0x02:
            return
        wp %= n
        if wp == self.rirb_rp:
            return
        base = self.base(RIRBLBASE, RIRBUBASE)
        mem = self.ram.read(base, n * 8)
        i = self.rirb_rp
        while i != wp:
            i = (i + 1) % n
            resp = int.from_bytes(mem[i * 8:i * 8 + 4], "little")
            ex = int.from_bytes(mem[i * 8 + 4:i * 8 + 8], "little")
            self.emit_resp(resp, ex)
        self.rirb_rp = wp

    def emit_verb(self, verb):
        self.nverbs += 1
        cad, nid, name, code, payload = decode(verb)
        self.verbs.write(f"{self.ts()} -> 0x{verb:08x}  cad={cad} nid=0x{nid:02x} {name} 0x{payload:x}\n")
        self.pending.append((verb, cad, nid, name, payload))
        # COEF tracking (index + value)
        if name == "SET_COEF_INDEX":
            self.coef_idx[nid] = payload
        elif name == "SET_PROC_COEF":
            idx = self.coef_idx.get(nid)
            if idx is not None:
                self.coef_shadow[(nid, idx)] = payload
                self.coef.write(f"{self.ts()} W nid=0x{nid:02x} COEF[0x{idx:02x}] = 0x{payload:04x}\n")
                if nid == 0x20 and idx == 0x26:
                    self.emit_amp_pack(payload)
                # the chip auto-increments the index after each access
                self.coef_idx[nid] = (idx + 1) & 0xFFFF
        elif name == "GET_PROC_COEF":
            idx = self.coef_idx.get(nid)
            if idx is not None:
                self.coef_idx[nid] = (idx + 1) & 0xFFFF

    def emit_amp_pack(self, cmd):
        g = lambda i: self.coef_shadow.get((0x20, i))
        amp, reg, d24, d25 = g(0x22), g(0x23), g(0x24), g(0x25)
        fmt = lambda v: "----" if v is None else f"{v:04x}"
        self.coef.write(
            f"{self.ts()}   AMP pack: amp=0x{fmt(amp)} reg=0x{fmt(reg)} "
            f"0x24=0x{fmt(d24)} 0x25=0x{fmt(d25)} cmd=0x{cmd:04x}\n")

    def emit_resp(self, resp, ex):
        if ex & 0x10:
            self.verbs.write(f"{self.ts()} <- UNSOL 0x{resp:08x} ex=0x{ex:08x}\n")
            return
        req = self.pending.pop(0) if self.pending else None
        if req:
            verb, cad, nid, name, payload = req
            self.verbs.write(f"{self.ts()} <- 0x{resp:08x}   (for 0x{verb:08x} {name})\n")
            if name == "GET_PROC_COEF":
                # the index was already auto-incremented: the one read is the previous one
                idx = (self.coef_idx.get(nid, 1) - 1) & 0xFFFF
                self.coef.write(f"{self.ts()} R nid=0x{nid:02x} COEF[0x{idx:02x}] -> 0x{resp & 0xFFFF:04x}\n")
        else:
            self.verbs.write(f"{self.ts()} <- 0x{resp:08x} (no pending verb)\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ram", required=True, help="memory-backend-file file")
    ap.add_argument("--low", type=lambda x: int(x, 0), default=0x80000000,
                    help="the VM's max-ram-below-4g (default 2 GiB)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--marks", default=None,
                    help="FIFO/text file where the user writes marks")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    # Minimal pipe: QEMU cannot get more than ~4 KiB of trace ahead,
    # so memory is read almost in lockstep with the guest.
    try:
        import fcntl
        fcntl.fcntl(sys.stdin.fileno(), 1031, 4096)  # F_SETPIPE_SZ
    except OSError as e:
        sys.stderr.write(f"[hda-trace] ERROR: could not shrink the pipe ({e}); "
                         "without that the capture is not reliable. Stopping.\n")
        sys.exit(3)
    ram = GuestRAM(a.ram, a.low)
    tr = Tracer(ram, a.out)
    marks_fh = None
    if a.marks:
        if not os.path.exists(a.marks):
            os.mkfifo(a.marks, 0o666)
        marks_fh = os.open(a.marks, os.O_RDONLY | os.O_NONBLOCK)

    last_report = time.time()
    for line in sys.stdin:
        if time.time() - last_report > 5:
            last_report = time.time()
            sys.stderr.write(f"[hda-trace] verbs captured: {tr.nverbs}\n")
        if marks_fh is not None:
            try:
                data = os.read(marks_fh, 4096)
                for m in data.decode(errors="replace").splitlines():
                    if m.strip():
                        tr.log_event("MARK: " + m.strip())
            except BlockingIOError:
                pass
        m = WRITE_RE.search(line)
        if m:
            if m.group(1) == "0":
                tr.on_write(int(m.group(2), 16), int(m.group(3), 16), int(m.group(4)))
            continue
        m = READ_RE.search(line)
        if m:
            if m.group(1) == "0":
                tr.on_read(int(m.group(2), 16), int(m.group(3)), int(m.group(4), 16))
            continue
        if "vfio" not in line:
            sys.stderr.write(line)  # regular QEMU messages
    tr.log_event(f"trace end ({tr.nverbs} verbs)")


if __name__ == "__main__":
    main()
