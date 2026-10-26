#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Search files (e.g. a Windows driver folder) for the captured filter coefficients.
Read only. Tries several encodings: 32-bit LE/BE, 3 raw bytes, hex text, UTF-16 text.

Usage: python3 -I find-table.py TABLE PATH [PATH ...]
  TABLE  fix/windows-amp-init-940xfg-ka1ar.txt
  PATH   files or folders to scan (files > 64 MiB are skipped)
Prints the files with the most distinct coefficient values found.
"""
import os, re, sys

if len(sys.argv) < 3:
    sys.exit(__doc__)
tab = [l.split() for l in open(sys.argv[1]) if l.startswith("0x")]
vals = {}
for amp, reg, hi, lo, cmd in tab:
    if cmd != "0xb017": continue
    v = (int(hi,16) << 16) | int(lo,16)
    if v in (0, 0x10): continue
    vals[v] = (amp, reg)
pats = {}
for v in vals:
    b3 = bytes([(v>>16)&255, (v>>8)&255, v&255])
    forms = {"le32": v.to_bytes(4,"little"), "be32": v.to_bytes(4,"big"),
             "b3": b3, "b3r": b3[::-1]}
    for t in ("%06x" % v, "%06X" % v, ",".join("%02x" % x for x in b3[::-1]), ",".join("%02x" % x for x in b3)):
        forms["txt:"+t] = t.encode(); forms["u16:"+t] = t.encode("utf-16-le")
    for k, p in forms.items(): pats.setdefault(p, set()).add((v, k.split(":")[0]))
rx = re.compile(b"|".join(re.escape(p) for p in sorted(pats, key=len, reverse=True)))
roots = sys.argv[2:]
res = []
for root in roots:
    paths = [root] if os.path.isfile(root) else [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs]
    for p in paths:
        try:
            if os.path.getsize(p) > 64 << 20: continue
            data = open(p, "rb").read()
        except OSError: continue
        hit = {}
        for m in rx.finditer(data):
            for v, k in pats[m.group(0)]: hit.setdefault(k, set()).add(v)
        if hit:
            best = max(hit, key=lambda k: len(hit[k]))
            res.append((len(hit[best]), best, p))
res.sort(reverse=True)
print(f"distinct values searched: {len(vals)}")
for n, k, p in res[:15]: print(f"{n:3d} {k:5s} {p}")
