#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Find (register, value) tables inside a Windows driver binary and match them
against the captured per-amp sequence. Read only.

Usage: python3 -I match-tables.py TABLE RTKVHD64.sys
  TABLE  fix/windows-amp-init-940xfg-ka1ar.txt

A "table" is a run of >= 8 aligned 32-bit pairs (reg in 0x2000-0x23ff, value < 2^24).
For each amp it prints the best-matching tables and, for 0x38 and 0x3c, what the
capture has before/after the matching part plus the 48 bytes preceding the table.
"""
import sys, struct

if len(sys.argv) != 3:
    sys.exit(__doc__)
data = open(sys.argv[2], "rb").read()
# captured sequence per amp: list of (reg, value) in order
cap = {}
for l in open(sys.argv[1]):
    if not l.startswith("0x"): continue
    a, r, h, lo, c = (int(x, 16) for x in l.split())
    cap.setdefault(a, []).append((r, (h << 16) | lo))
# tables in the binary: runs of (reg 0x2000-0x23ff, value < 2^24) pairs, 4-byte aligned
tables = []
n = len(data) // 4
w = struct.unpack("<%dI" % n, data[:n*4])
i = 0
while i < n - 1:
    j = i; run = []
    while j < n - 1 and 0x2000 <= w[j] <= 0x23ff and w[j+1] < (1 << 24):
        run.append((w[j], w[j+1])); j += 2
    if len(run) >= 8:
        tables.append((i*4, run)); i = j
    else:
        i += 1
print(f"tables found in the driver: {len(tables)}")
def score(run, seq):
    # longest contiguous match of the start of run anywhere inside seq
    best = 0
    for k in range(len(seq)):
        m = 0
        while k+m < len(seq) and m < len(run) and seq[k+m] == run[m]: m += 1
        best = max(best, m)
    return best
for a in (0x38, 0x39, 0x3c, 0x3d):
    seq = cap[a]
    res = sorted(((score(run, seq), off, len(run)) for off, run in tables), reverse=True)[:3]
    print(f"amp 0x{a:02x}: {len(seq)} captured writes; best tables:",
          ", ".join(f"[0x{off:x} len={ln} matches {s} in a row]" for s, off, ln in res))
# detail: best table for 0x38 and 0x3c, and its header
for a in (0x38, 0x3c):
    seq = cap[a]
    s, off, run = max(((score(r, seq), o, r) for o, r in tables), key=lambda x: x[0])
    k = next(k for k in range(len(seq)) if seq[k:k+s] == run[:s])
    print(f"\namp 0x{a:02x}: table 0x{off:x} ({len(run)} pairs) matches the capture from write #{k} ({seq[k][0]:#06x}) for {s} pairs")
    print("  capture before the table:", " ".join(f"{r:04x}={v:x}" for r, v in seq[:k]))
    rest = seq[k+s:]
    print("  capture after:", " ".join(f"{r:04x}={v:x}" for r, v in rest[:12]), "..." if len(rest) > 12 else "")
    print("  table after:", " ".join(f"{r:04x}={v:x}" for r, v in run[s:s+12]))
    hdr = struct.unpack("<12I", data[off-48:off])
    print("  header:", " ".join("%08x" % x for x in hdr))
