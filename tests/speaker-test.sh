#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Interactive speaker test with the Windows configuration.
# Usage (from the repo root):  sudo ./tests/speaker-test.sh
# Each tone only plays after you press Enter. 's' skips, 'q' quits.
# On exit (always, even with Ctrl+C): disables the amplifiers and restores the
# codec's runtime power management.
#
# Expected (verified on a 940XFG KA1AR, 2026-10-26):
#   1 all 4 amps, 1 kHz     -> both channels
#   2 tweeters, 4 kHz       -> both channels
#   3 tweeters, 200 Hz      -> (almost) nothing: the crossover filter works
#   4 woofers, 200 Hz       -> both channels, quiet
#   5 all 4, pink noise     -> both channels
set -u
D=$(cd "$(dirname "$0")/.." && pwd)
INIT="python3 -I $D/fix/alc298-amp-init-win.py --table $D/fix/windows-amp-init-940xfg-ka1ar.txt"
DEV=plughw:sofhdadsp,0
PWR=/sys/bus/hdaudio/devices/ehdaudio0D0/power/control
NOTES=/tmp/speaker-test-notes.txt

[ "$(id -u)" = 0 ] || { echo "run it with sudo"; exit 1; }
PREV=$(cat "$PWR")

finish() {
  $INIT --off >/dev/null 2>&1 && echo ">> amplifiers disabled"
  echo "$PREV" > "$PWR" && echo ">> codec power management restored ($PREV)"
}
trap finish EXIT

echo on > "$PWR"
echo ">> codec kept awake during the test (power/control=on)"
amixer -c sofhdadsp -- sset Master -20dB >/dev/null
amixer -c sofhdadsp -- sset Speaker 0dB >/dev/null
echo ">> volume: Master $(amixer -c sofhdadsp get Master | grep -o '\[-\?[0-9.]*dB\]' | head -1), Speaker $(amixer -c sofhdadsp get Speaker | grep -o '\[-\?[0-9.]*dB\]' | head -1)"

$INIT --no-enable || exit 1     # apply the full Windows config (amps left disabled)

step() {  # step "description" amps frequency|pink
  local desc=$1 amps=$2 f=$3 r
  echo
  echo "=== $desc ==="
  read -r -p "Enter = play (left, then right), s = skip, q = quit: " r
  case "$r" in q) exit 0 ;; s) return ;; esac
  $INIT --enable-only "$amps" || exit 1
  if [ "$f" = pink ]; then
    speaker-test -D $DEV -c 2 -t pink -l 1 2>&1 | grep -E "Front|rror|busy"
  else
    speaker-test -D $DEV -c 2 -t sine -f "$f" -l 1 2>&1 | grep -E "Front|rror|busy"
  fi
  $INIT --off >/dev/null
  read -r -p "  what did you hear? (type it and press Enter) " r
  echo "  [$desc] -> $r" >> "$NOTES"
}

: > "$NOTES"
step "1 CONTROL: all 4 amplifiers, 1000 Hz"      38,39,3c,3d 1000
step "2 TWEETERS only, 4000 Hz (should play)"    3c,3d       4000
step "3 TWEETERS only, 200 Hz (almost nothing)"  3c,3d       200
step "4 WOOFERS only, 200 Hz (should play)"      38,39       200
step "5 all 4, pink noise"                       38,39,3c,3d pink
echo
echo "Notes saved in $NOTES"
