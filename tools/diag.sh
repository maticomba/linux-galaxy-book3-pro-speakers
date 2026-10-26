#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Collect a read-only audio diagnostic of the machine (model, kernel, codecs, amps, modules, logs).
# Usage: ./diag.sh > diag.txt   (some sections are more complete with sudo)
echo "== DMI =="; for f in sys_vendor product_name product_version board_name bios_version; do printf "%s: " $f; cat /sys/class/dmi/id/$f 2>/dev/null; done
echo "== OS / kernel =="; grep PRETTY_NAME /etc/os-release; uname -r
echo "== Secure Boot =="; mokutil --sb-state 2>/dev/null || echo "mokutil not available"
echo "== Cards =="; cat /proc/asound/cards; aplay -l 2>/dev/null
echo "== Codecs =="; for c in /proc/asound/card*/codec#*; do echo "--- $c"; grep -E "^Codec|Vendor Id|Subsystem Id|Revision Id" "$c"; done
echo "== ACPI amps (MAX98390/CS35L41/TAS) =="; ls /sys/bus/acpi/devices/ 2>/dev/null | grep -iE "MX98|CSC3|TIAS|TXNW|10EC" || echo "none"
echo "== I2C devices =="; for d in /sys/bus/i2c/devices/*; do [ -f $d/name ] && echo "$(basename $d): $(cat $d/name)"; done
echo "== snd modules =="; lsmod | grep -E "^snd_(hda|soc|sof)|max98|cs35|tas2"
echo "== modprobe.d audio =="; grep -rHE "snd|hda" /etc/modprobe.d/ 2>/dev/null
echo "== cmdline =="; cat /proc/cmdline
echo "== Kernel log (audio) =="; journalctl -k -b --no-pager 2>/dev/null | grep -iE "snd|hda|sof|alc298|max98|realtek|audio" | tail -40
echo "== Sound server =="; pactl info 2>/dev/null | grep -E "Server Name|Default Sink"; pactl list short sinks 2>/dev/null
echo "== hda-verb =="; command -v hda-verb || echo "hda-verb not installed"
