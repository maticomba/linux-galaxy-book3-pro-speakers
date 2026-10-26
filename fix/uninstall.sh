#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Uninstall the Windows amplifier configuration and disable the amplifiers.
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run it with sudo"; exit 1; }
[ -x /usr/local/sbin/alc298-amp-init-win.py ] && /usr/bin/python3 -I /usr/local/sbin/alc298-amp-init-win.py --off || true
systemctl stop alc298-amp-win.service 2>/dev/null || true
rm -f /etc/systemd/system/alc298-amp-win.service /etc/udev/rules.d/99-alc298-amp-win.rules \
      /lib/systemd/system-sleep/alc298-amp-win /usr/local/sbin/alc298-amp-init-win.py
rm -rf /usr/local/share/alc298-amp-win
systemctl daemon-reload; udevadm control --reload-rules
echo "Uninstalled. After a reboot the amplifiers return to their factory state (disabled)."
