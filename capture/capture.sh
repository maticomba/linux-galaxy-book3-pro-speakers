#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Start the Windows VM with HDA verb tracing and store the logs in captures/<date>/.
# Read only: QEMU logs the register accesses and hda-trace.py reads guest RAM.
# Usage (from this folder):        sudo ./capture.sh
# Marks (from another terminal):   echo "text" > /tmp/hda-mark
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
W=${WORKDIR:-$D}
OUT=$W/captures/$(date +%Y%m%d-%H%M%S)
export RAMFILE=/dev/shm/hda-winvm-ram
MARKS=/tmp/hda-mark

mkdir -p "$OUT"
rm -f "$RAMFILE"
[ -p "$MARKS" ] || mkfifo -m 666 "$MARKS"
echo "Logs in: $OUT"
echo "Marks:   echo 'text' > $MARKS"

# QEMU stderr (the trace) -> hda-trace.py ; QEMU stdout -> /dev/null
TRACE=1 "$D/run-win.sh" 2>&1 >/dev/null \
  | python3 -I "$D/hda-trace.py" --ram "$RAMFILE" --low 0x80000000 --out "$OUT" --marks "$MARKS" || true

rm -f "$RAMFILE"   # free the 4 GiB of shared RAM
chown -R "${SUDO_USER:-$(logname)}:" "$W/captures"
echo "Capture finished: $OUT"
wc -l "$OUT"/*.log
