#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Show where a few known coefficient values sit inside a binary (e.g. RTKVHD64.sys),
with a hex dump of the surrounding 32-bit words, and count occurrences of the
codec SSID. Read only.

Usage: python3 -I inspect-table.py RTKVHD64.sys
"""
import sys, struct

if len(sys.argv) != 2:
    sys.exit(__doc__)
data = open(sys.argv[1], "rb").read()
def le(v): return struct.pack("<I", v)
# first coefficient of a tweeter table and of a main-amp table (from the capture)
targets = {"tw_2128": 0x0005460d, "main_2100": 0x00ccbf0e}
for name, v in targets.items():
    offs = []; i = data.find(le(v))
    while i != -1: offs.append(i); i = data.find(le(v), i + 1)
    print(f"{name} 0x{v:08x}: {len(offs)} occurrences at", [hex(o) for o in offs[:12]])
    for o in offs[:3]:
        print(f"  context of 0x{o:x}:")
        base = o - 48
        for row in range(10):
            p = base + row*16
            words = struct.unpack("<4I", data[p:p+16])
            print("   0x%07x: " % p + " ".join("%08x" % w for w in words))
# look for the SSID in the binary
for s in (0x144dc882, 0xc882144d):
    n = data.count(le(s)); print(f"SSID LE32 0x{s:08x}: {n} occurrences")
for s in (b"144DC882", b"144dc882", "144DC882".encode("utf-16-le")):
    print(f"text {s[:16]!r}: {data.count(s)}")
