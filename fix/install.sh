#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Install the Windows amplifier configuration for the Galaxy Book3 Pro 14" (940XFG).
# Usage (from this folder):  sudo ./install.sh
set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run it with sudo"; exit 1; }

# Safeguards
[ "$(cat /sys/class/dmi/id/product_name)" = 940XFG ] || { echo "ABORT: this model is not a 940XFG"; exit 1; }
ok=0
for d in /sys/class/sound/hwC*D*; do
  [ "$(cat $d/vendor_id 2>/dev/null)" = 0x10ec0298 ] && [ "$(cat $d/subsystem_id 2>/dev/null)" = 0x144dc882 ] && ok=1
done
[ $ok = 1 ] || { echo "ABORT: ALC298 codec with SSID 0x144dc882 not found"; exit 1; }
if [ -e /etc/systemd/system/alc298-amp-init.service ] || [ -e /usr/local/sbin/alc298-amp-init.sh ]; then
  echo "ABORT: Andycodeman's speaker-fix-940xfg is installed. Uninstall it first (its uninstall.sh)."; exit 1
fi
python3 -I ./alc298-amp-init-win.py --dry-run --table ./windows-amp-init-940xfg-ka1ar.txt >/dev/null \
  || { echo "ABORT: the table or the script fail the dry run"; exit 1; }

install -m 755 alc298-amp-init-win.py /usr/local/sbin/alc298-amp-init-win.py
install -d /usr/local/share/alc298-amp-win
install -m 644 windows-amp-init-940xfg-ka1ar.txt /usr/local/share/alc298-amp-win/
install -m 644 alc298-amp-win.service /etc/systemd/system/alc298-amp-win.service
install -m 644 99-alc298-amp-win.rules /etc/udev/rules.d/99-alc298-amp-win.rules
install -d /lib/systemd/system-sleep
install -m 755 alc298-amp-win-sleep /lib/systemd/system-sleep/alc298-amp-win
systemctl daemon-reload
udevadm control --reload-rules
# Check (simulation only, changes nothing) that the rule fires only for the internal card
for c in /sys/class/sound/controlC*; do
  if udevadm test --action=add "$c" 2>&1 | grep -q "SYSTEMD_WANTS=.*alc298-amp-win.service"; then
    echo "  udev rule: $(basename $c) -> triggers alc298-amp-win ($(basename $(readlink -f $c/device)))"
  else
    echo "  udev rule: $(basename $c) -> does not trigger"
  fi
done
echo "Installed. Applying now:"
systemctl restart alc298-amp-win.service
systemctl --no-pager --lines=5 status alc298-amp-win.service | sed -n '1,12p' || true
