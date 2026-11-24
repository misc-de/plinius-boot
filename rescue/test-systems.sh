#!/bin/bash
# Installing, moving and swapping systems, tested against a real rescue system in QEMU with an emulated
# SD card, so that the same scripts run as on the phone: /sys/block/mmcblk0/device/type says SD. The card
# is laid out as on a Plinius with Ubuntu Touch and Arch, plus two free partitions. Nothing here touches a
# phone. Needs a built ramdisk (rescue/mkrescue.sh) and the kernel from tools/chroot-init.sh.
set -euo pipefail
. "$(dirname "$0")/../tools/lib.sh"

ramdisk=$BUILD/rescue/plinius-rescue-aarch64.cpio.lz4
[[ -f $ramdisk ]] || die "no rescue ramdisk, run rescue/mkrescue.sh first"
kpkg=$(ls "$PKGCACHE"/linux-aarch64-[0-9]*.pkg.tar.* 2>/dev/null | grep -v '\.sig$' | sort -V | tail -n 1)
[[ -n $kpkg ]] || die "linux-aarch64 not in $PKGCACHE (run tools/chroot-init.sh)"
app=$ROOT/plinius-boot
out=$BUILD/systems-test
port=45457
serial=SYSTEST1

rm -rf "$out"
mkdir -p "$out/kernel" "$out/config" "$out/vendor/lib/modules"
msg "Kernel from $(basename "$kpkg")"
tar -xf "$kpkg" -C "$out/kernel" boot/Image usr/lib/modules
# What the test machine needs and the kernel only has as modules: network, disk, PCI and the SD host.
# The package has no modules.dep; the rescue system retries until no more load, as on the phone.
# (busybox grep -v with an empty pattern file drops everything, so the recovery list must not be empty)
echo virtio_mmio.ko > "$out/vendor/lib/modules/modules.load.recovery"
for m in virtio_mmio failover net_failover virtio_net virtio_blk pci-host-common pci-host-generic \
	mmc_core mmc_block cqhci led-class sdhci sdhci-uhs2 sdhci-pci loop; do
	f=$(find "$out/kernel/usr/lib/modules" -name "$m.ko" | head -n 1)
	[[ -n $f ]] || continue
	cp "$f" "$out/vendor/lib/modules/"
	echo "$m.ko" >> "$out/vendor/lib/modules/modules.load"
done
(cd "$out/vendor" && find . | LC_ALL=C sort | cpio -o -H newc -R 0:0 --quiet) | lz4 -l -9 -q > "$out/vendor.cpio.lz4"

# Rescue ramdisk and second archive exactly as the app builds them (key in $out/config)
XDG_CONFIG_HOME=$out/config python3 -I -B -c '
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("plinius_boot", sys.argv[1])
app = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(app)
sys.stdout.buffer.write(app.rescue_ramdisk_data(sys.argv[2], ""))
' "$app" "$serial" > "$out/rescue.cpio"
key=$out/config/plinius-boot/rescue_ed25519
[[ -f $key ]] || die "the app created no key"

# The card. An SD card must have a size QEMU accepts, a power of two is safe. Types as the manager sets
# them: 8305 system, 8302 data, which is what makes systemd mount the first one as /home.
card=$out/card.img
truncate -s 512M "$card"
sfdisk -q "$card" <<-EOF
	label: gpt
	start=2048, size=32768, name=abm_settings, type=8DA63339-0007-60C0-C436-083AC8230908
	start=34816, size=65536, type=B921B045-1DF0-41C3-AF44-4C6F280D3FAE
	start=100352, size=131072, type=933AC7E1-2EB4-4F13-B844-0E14E2AEF915
	start=231424, size=65536, type=B921B045-1DF0-41C3-AF44-4C6F280D3FAE
	start=296960, size=524288, type=933AC7E1-2EB4-4F13-B844-0E14E2AEF915
	start=821248, size=65536, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
	start=886784, size=131072, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
EOF

# fill SECTOR SECTORS TREE: a file system with the contents of TREE into the card
fill() {
	# A new file every time: mkfs does not shrink one that is already there, and dd would write all of it
	rm -f "$out/part.img"
	mkfs.ext4 -q -F -d "$3" "$out/part.img" "$(($2 / 2))k"
	dd if="$out/part.img" of="$card" bs=512 seek="$1" conv=notrunc status=none
}
tree() {
	rm -rf "$out/tree"
	mkdir -p "$out/tree/etc"
	echo "$1" > "$out/tree/marker"
}
tree ut-system
printf '%s\n' 'NAME="Ubuntu"' 'VERSION_ID="24.04"' > "$out/tree/etc/os-release"
mkdir -p "$out/tree/usr/lib/systemd/system-generators" "$out/tree/etc/systemd/system-generators"
touch "$out/tree/usr/lib/systemd/system-generators/systemd-gpt-auto-generator"
# Ubuntu Touch switches the generator off
ln -s /dev/null "$out/tree/etc/systemd/system-generators/systemd-gpt-auto-generator"
fill 34816 65536 "$out/tree"
tree ut-data
fill 100352 131072 "$out/tree"
tree arch-system
printf '%s\n' 'NAME="Arch Linux ARM"' 'PRETTY_NAME="Arch Linux ARM"' > "$out/tree/etc/os-release"
mkdir -p "$out/tree/usr/lib/systemd/system-generators"
touch "$out/tree/usr/lib/systemd/system-generators/systemd-gpt-auto-generator"
fill 231424 65536 "$out/tree"
tree arch-data
fill 296960 524288 "$out/tree"

# The boot entries, written as the Volla Boot Manager does
rm -rf "$out/tree"
mkdir -p "$out/tree/db/entries" "$out/tree/rom0" "$out/tree/rom1"
printf '%s\n' 'default Entry 01' 'timeout 5' > "$out/tree/db/db.conf"
printf '%s\n' 'dtb null' 'xtype droid' 'xpart real' 'linux null' 'options null' 'title VollaOS' 'initrd null' \
	'logo real/logo.bin' > "$out/tree/db/entries/real.conf"
printf '%s\n' 'dtb rom0/dtb.dtb' 'xtype UT' 'xpart 2:3' 'linux rom0/zImage' \
	'options bootopt=64S3,32N2,64N2 androidboot.selinux=permissive rootdelay=10 systempart=/dev/mmcblk0p2 datapart=/dev/mmcblk0p3' \
	'title Ubuntu Touch' 'initrd rom0/initrd.cpio.gz' 'logo rom0/logo.bin' > "$out/tree/db/entries/rom0.conf"
printf '%s\n' 'dtb rom0/dtb.dtb' 'xtype UT' 'xpart 4:5' 'linux rom0/zImage' \
	'options bootopt=64S3,32N2,64N2 androidboot.selinux=permissive systempart=/dev/mmcblk0p4 datapart=/dev/mmcblk0p5' \
	'title Arch' 'initrd rom0/initrd.cpio.gz' 'logo rom1/logo.bin' > "$out/tree/db/entries/rom1.conf"
fill 2048 32768 "$out/tree"

# The phone's own storage: sectors of 4096 bytes, as measured on the Plinius, with what must never change
# around userdata. Numbers in sectors of the disk.
truncate -s 640M "$out/internal.img"
sfdisk -q --sector-size 4096 "$out/internal.img" <<-EOF
	label: gpt
	start=256, size=256, name=nvdata
	start=512, size=2048, name=super
	start=2560, size=127440, name=userdata, type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7
	start=130000, size=1024, name=mrdump
	start=131072, size=2048, name=boot_b
	start=133120, size=2048, name=vendor_boot_b
	start=135168, size=2048, name=init_boot_b
EOF
head -c 1M /dev/urandom | dd of="$out/internal.img" bs=4096 seek=256 conv=notrunc status=none
head -c 4M /dev/urandom | dd of="$out/internal.img" bs=4096 seek=130000 conv=notrunc status=none
head -c 8M /dev/urandom | dd of="$out/internal.img" bs=4096 seek=131072 conv=notrunc status=none

cat "$out/vendor.cpio.lz4" "$out/rescue.cpio" > "$out/initrd"
log=$out/serial.log
qemu-system-aarch64 -machine virt -cpu max,pauth-impdef=on -smp 2 -m 1024 -display none \
	-global virtio-mmio.force-legacy=false -kernel "$out/kernel/boot/Image" -initrd "$out/initrd" \
	-append "console=ttyAMA0 PLINIUS_RESCUE_NETDEV=eth0 PLINIUS_RESCUE_NODEVTMPFS=1" \
	-drive if=none,id=d,file="$out/internal.img",format=raw -device virtio-blk-device,drive=d,logical_block_size=4096,physical_block_size=4096 \
	-device sdhci-pci,id=sdhci -drive if=none,id=sd,file="$card",format=raw -device sd-card,drive=sd \
	-netdev user,id=n,net=10.15.19.0/24,host=10.15.19.2,hostfwd=tcp:127.0.0.1:$port-10.15.19.82:22 \
	-device virtio-net-device,netdev=n -serial file:"$log" -no-reboot &
qpid=$!
trap 'kill $qpid 2>/dev/null || true' EXIT

on_phone() {
	ssh -p $port -i "$key" -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
		-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@127.0.0.1 "$@"
}
for ((i = 0; i < 150; i++)); do
	on_phone true 2>/dev/null && break
	kill -0 $qpid 2>/dev/null || die "QEMU exited, see $log"
	sleep 2
done
on_phone true || die "no ssh login, see $log"
on_phone 'for i in $(seq 40); do [ -e /sys/block/mmcblk0/device/type ] && exit 0; sleep 1; done; exit 1' ||
	die "the emulated SD card did not appear: $(on_phone 'ls /sys/block; dmesg | grep -i -E "mmc|sdhci" | tail -5')"
[[ $(on_phone cat /sys/block/mmcblk0/device/type) == SD ]] || die "the card does not report as SD"
msg "SD card: $(on_phone cat /sys/block/mmcblk0/size) sectors, type SD, as on the phone"
# As on the phone with its kernel, /dev comes from mdev, and the partition links must be there too
on_phone 'for i in $(seq 40); do [ -e /run/plinius-rescue-starting ] || break; sleep 1; done; plinius-partlinks; ls /dev/mmcblk0p1 /dev/disk/by-partlabel/userdata >/dev/null' ||
	die "no device nodes for the card and the storage"
[[ $(on_phone blockdev --getss /dev/vda) == 4096 ]] || die "the internal disk should have sectors of 4096 bytes"

python3 -I -B "$ROOT/rescue/test-systems.py" "$app" "$key" "$port" "$out"
on_phone poweroff || true
