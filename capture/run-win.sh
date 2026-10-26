#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Boot the real dual-boot Windows inside QEMU with the audio controller (00:1f.3)
# passed through with VFIO.
# The real disk is NOT written: everything goes to win-overlay.qcow2, whose
# backing file (the physical NVMe) is opened read-only.
#
# Usage:  sudo ./run-win.sh                 normal run
#         sudo NOAUDIO=1 ./run-win.sh       without the audio device
#         (with tracing: use capture.sh, which sets TRACE=1)
#
# Files expected in $WORKDIR (default: this folder):
#   win-overlay.qcow2  qemu-img create -f qcow2 -F raw -b /dev/nvme0n1 win-overlay.qcow2
#   OVMF_VARS.fd       cp /usr/share/OVMF/OVMF_VARS_4M.fd OVMF_VARS.fd
#   nhlt.bin           sudo cp /sys/firmware/acpi/tables/NHLT nhlt.bin
# The SMBIOS strings below are the ones of the author's machine (940XFG,
# NP940XFG-KA1AR); adjust them from /sys/class/dmi/id/ on yours.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
W=${WORKDIR:-$D}
DISK=${DISK:-/dev/nvme0n1}
QEMU=${QEMU:-qemu-system-x86_64}

# Safeguards: audio must be bound to vfio-pci and the rest of IOMMU group 16 must have no driver.
[ "$(basename "$(readlink /sys/bus/pci/devices/0000:00:1f.3/driver)")" = vfio-pci ] || { echo >&2 "ABORT: 00:1f.3 is not bound to vfio-pci"; exit 1; }
for a in 1f.0 1f.4 1f.5; do
  [ -e /sys/bus/pci/devices/0000:00:$a/driver ] && { echo >&2 "ABORT: 00:$a has a driver"; exit 1; }
done
[ -f "$W/win-overlay.qcow2" ] || { echo >&2 "ABORT: overlay missing"; exit 1; }
qemu-img info "$W/win-overlay.qcow2" | grep -q "backing file: $DISK" || { echo >&2 "ABORT: the overlay does not point to $DISK"; exit 1; }

# The user's display (sudo drops these variables).
U=${SUDO_USER:-$(logname)}
export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-$(ls /run/user/"$(id -u "$U")"/.mutter-Xwaylandauth.* 2>/dev/null | head -1)}
export GDK_BACKEND=x11

AUDIO=(-device vfio-pci,host=0000:00:1f.3)
EXTRA=()
MACHINE=q35,accel=kvm
if [ "${NOAUDIO:-0}" = 1 ]; then
  AUDIO=(); echo >&2 "Booting WITHOUT audio"
elif [ "${TRACE:-0}" = 1 ]; then
  # x-no-mmap: every register access goes through QEMU and shows up in the trace (slower, same behaviour).
  AUDIO=(-device vfio-pci,host=0000:00:1f.3,x-no-mmap=on,x-intx-mmap-timeout-ms=0)
  # Guest RAM goes to a shared file so hda-trace.py can read it without stopping QEMU.
  EXTRA=(-trace enable=vfio_region_write -trace enable=vfio_region_read
         -object memory-backend-file,id=ram0,size=4G,mem-path=${RAMFILE:?RAMFILE not set},share=on)
  MACHINE=q35,accel=kvm,memory-backend=ram0,max-ram-below-4g=2G
  echo >&2 "Booting WITH HDA tracing"
fi

# -cpu host,-vmx is required: with hypervisorlaunchtype=Auto, Windows otherwise
# loops into automatic repair.
exec "$QEMU" \
  -name win-audio-trace \
  -machine $MACHINE -cpu host,-vmx -smp 4 -m 4096 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=$W/OVMF_VARS.fd \
  -drive id=win,if=none,format=qcow2,file=$W/win-overlay.qcow2 \
  -device nvme,drive=win,serial=WINOVERLAY \
  "${AUDIO[@]}" \
  -acpitable file=$W/nhlt.bin \
  -smbios "type=0,vendor=American Megatrends International,, LLC.,version=P03RGS.150.230330.ZW" \
  -smbios "type=1,manufacturer=SAMSUNG ELECTRONICS CO.,, LTD.,product=940XFG,version=P03RGS" \
  -smbios "type=2,manufacturer=SAMSUNG ELECTRONICS CO.,, LTD.,product=NP940XFG-KA1AR" \
  -nic none \
  -usb -device usb-tablet \
  -display gtk -vga std \
  "${EXTRA[@]}" \
  "$@"
