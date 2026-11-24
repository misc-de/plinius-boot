#!/bin/bash
# Automated test of the rescue system (rescue/mkrescue.sh, rescue/rescue-init) in QEMU (aarch64 virt,
# kernel from the cached linux-aarch64 package). The ramdisk is assembled as on the phone:
#   vendor ramdisk (lz4, modules + lists) + rescue ramdisk + the second archive of plinius-boot
#   (SSH key, serial number, accent colour RESCUE_ACCENT, default slate; empty: Arch blue), the
#   latter made by the app's own rescue_ramdisk_data().
# The test vendor ramdisk lists its modules out of dependency order and with one missing module,
# like the Plinius' modules.load. As there, the recovery list lacks modules that others need
# (failover, the virtio-gpu stack), which only modules.load has. QEMU has no USB device controller,
# so PLINIUS_RESCUE_NETDEV=eth0 puts the phone's address on a virtio network card, reached through
# a port forward.
# Checks: modules and their retries, recovery list first and then the rest of modules.load,
# partition links and the app's partition list and system detection (os-release of an ext4
# partition, which stays unchanged), reading and writing a partition, the screen
# (plinius-splash on a virtio graphics card: picture shown, status line changes, screenshots
# screen-1.png and screen-2.png), the tools archive (install_rescue_tools of the app, sent once only;
# cryptsetup checks a passphrase on a LUKS2 partition, dmsetup reaches the kernel driver without
# udev; dm-crypt itself needs modules this kernel does not have in the ramdisk, opening was tested
# on the phone), dropbear restarted after it is killed, and leaving the system
# with plinius-reboot bootloader, busybox reboot and busybox poweroff.
# Boot 1 runs without devtmpfs (/dev from mdev, as with the GKI kernel of Volla OS), boot 2 with it.
# Optional boot 0 with the phone's own kernel and vendor ramdisk (RESCUE_PHONE_BOOT=boot.img,
# RESCUE_PHONE_VENDOR_BOOT=vendor_boot.img, e.g. those of Ubuntu Touch for the Plinius): only checks
# that the kernel unpacks the chain and starts /init. The vendor modules then crash in QEMU
# (aee_hangdet reads watchdog registers that QEMU does not have), which is expected.
# Output in build/rescue-test (disk.img, serial-*.log, the ssh key).
set -euo pipefail
. "$(dirname "$0")/../tools/lib.sh"

ramdisk=$BUILD/rescue/plinius-rescue-aarch64.cpio.lz4
[[ -f $ramdisk ]] || die "no rescue ramdisk, run rescue/mkrescue.sh first"
[[ -f $BUILD/rescue/plinius-rescue-tools-aarch64.tar.gz ]] || die "no tools archive, run rescue/mkrescue.sh first"
kpkg=$(ls "$PKGCACHE"/linux-aarch64-[0-9]*.pkg.tar.* 2>/dev/null | grep -v '\.sig$' | sort -V | tail -n 1)
[[ -n $kpkg ]] || die "linux-aarch64 not in $PKGCACHE (run tools/chroot-init.sh)"
app=$ROOT/plinius-boot
out=$BUILD/rescue-test
port=45456
serial=TEST1234

rm -rf "$out"
mkdir -p "$out/kernel" "$out/vendor/lib/modules" "$out/config"
msg "Kernel from $(basename "$kpkg")"
tar -xf "$kpkg" -C "$out/kernel" boot/Image usr/lib/modules
mods=$(echo "$out"/kernel/usr/lib/modules/*/kernel)
for m in drivers/virtio/virtio_mmio drivers/net/virtio_net drivers/net/net_failover net/core/failover \
	drivers/gpu/drm/virtio/virtio-gpu drivers/virtio/virtio_dma_buf drivers/gpu/drm/drm_shmem_helper \
	drivers/gpu/drm/clients/drm_client_lib; do
	cp "$mods/$m.ko" "$out/vendor/lib/modules/"
done
# virtio_net needs net_failover needs failover, which only modules.load has (like vcp_status on
# the Plinius): the recovery list loads virtio_mmio only, the rest of modules.load and the retries
# take two more passes; missing.ko never loads. virtio-gpu comes before its helpers. virtio_mmio and
# virtio_net are in both lists and must be loaded once only.
printf '%s\n' virtio_net.ko net_failover.ko missing.ko virtio_mmio.ko \
	> "$out/vendor/lib/modules/modules.load.recovery"
printf '%s\n' virtio_mmio.ko virtio-gpu.ko virtio_dma_buf.ko drm_shmem_helper.ko drm_client_lib.ko \
	failover.ko virtio_net.ko > "$out/vendor/lib/modules/modules.load"
(cd "$out/vendor" && find . | LC_ALL=C sort | cpio -o -H newc -R 0:0 --quiet) | lz4 -l -9 -q > "$out/vendor.cpio.lz4"

# Rescue ramdisk and second archive exactly as the app builds them (key in $out/config)
XDG_CONFIG_HOME=$out/config python3 -I -c '
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("plinius_boot", sys.argv[1])
app = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(app)
sys.stdout.buffer.write(app.rescue_ramdisk_data(sys.argv[2], sys.argv[5]))
open(sys.argv[3], "w").write(app.LIST_SCRIPT)
open(sys.argv[4], "w").write(app.SYSTEMS_SCRIPT)
' "$app" "$serial" "$out/list.sh" "$out/systems.sh" "${RESCUE_ACCENT-slate}" > "$out/rescue.cpio"
# The colour the logo should have
accent_hex=$(python3 -I -c '
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("plinius_boot", sys.argv[1])
app = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(app)
print(app.ACCENT_COLORS.get(sys.argv[2], ""))
' "$app" "${RESCUE_ACCENT-slate}")
key=$out/config/plinius-boot/rescue_ed25519
[[ -f $key ]] || die "the app created no key"
cat "$out/vendor.cpio.lz4" "$out/rescue.cpio" > "$out/initrd"

# Disk with GPT names as on the phone; nvdata gets random content to read back
truncate -s 64M "$out/disk.img"
sfdisk -q "$out/disk.img" <<-EOF
	label: gpt
	start=2048, size=2048, name=nvdata
	start=4096, size=8192, name=boot_a
	start=12288, size=16384, name=super
	start=28672, size=16384, name=linux
	start=45056, size=16384, name=userdata
EOF
head -c 1M /dev/urandom > "$out/nvdata"
dd if="$out/nvdata" of="$out/disk.img" bs=512 seek=2048 conv=notrunc status=none
head -c 64K /dev/urandom > "$out/boot_a"
# A Linux system for the app's system detection; os-release as an absolute link, as on Arch
mkdir -p "$out/linux/etc" "$out/linux/usr/lib"
printf '%s\n' 'NAME="Arch Linux ARM"' 'PRETTY_NAME="Arch Linux ARM"' BUILD_ID=rolling IMAGE_VERSION=2026.11.02 \
	> "$out/linux/usr/lib/os-release"
ln -s /usr/lib/os-release "$out/linux/etc/os-release"
mkfs.ext4 -q -F -d "$out/linux" "$out/linux.img" 8M
dd if="$out/linux.img" of="$out/disk.img" bs=512 seek=28672 conv=notrunc status=none
# LUKS2 like the phone's userdata, small header and a fast key derivation for the test
truncate -s 8M "$out/userdata.img"
printf plinius-test | cryptsetup luksFormat -q --type luks2 --pbkdf pbkdf2 --pbkdf-force-iterations 1000 \
	--luks2-metadata-size 16k --luks2-keyslots-size 256k --disable-locks --key-file - "$out/userdata.img"
dd if="$out/userdata.img" of="$out/disk.img" bs=512 seek=45056 conv=notrunc status=none

qpid=
trap '[[ -z $qpid ]] || kill $qpid 2>/dev/null || true' EXIT

# start_vm N [PARAM...]: boots the rescue system in a new QEMU (exits on reboot/poweroff), log
# serial-N.log, PARAM on the kernel command line
start_vm() {
	log=$out/serial-$1.log
	# Modern virtio-mmio: virtio-gpu refuses legacy devices. The screen in portrait like a phone's.
	qemu-system-aarch64 -machine virt -cpu max,pauth-impdef=on -smp 2 -m 1024 -display none \
		-global virtio-mmio.force-legacy=false -kernel "$out/kernel/boot/Image" -initrd "$out/initrd" \
		-append "console=ttyAMA0 PLINIUS_RESCUE_NETDEV=eth0 ${*:2}" \
		-drive if=none,id=d,file="$out/disk.img",format=raw -device virtio-blk-device,drive=d \
		-netdev user,id=n,net=10.15.19.0/24,host=10.15.19.2,hostfwd=tcp:127.0.0.1:$port-10.15.19.82:22 \
		-device virtio-net-device,netdev=n -device virtio-gpu-device,xres=1080,yres=2400 \
		-monitor unix:"$out/monitor.sock",server=on,wait=off -serial file:"$log" -no-reboot &
	qpid=$!
}

# on_phone CMD: runs CMD in the rescue system as root, with the app's key
on_phone() {
	ssh -p $port -i "$key" -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
		-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@127.0.0.1 "$@"
}

# wait_ssh SECONDS: until a login works
wait_ssh() {
	local i
	for ((i = 0; i < $1; i += 2)); do
		on_phone true 2>/dev/null && return 0
		kill -0 $qpid 2>/dev/null || die "QEMU exited, see $log"
		sleep 2
	done
	die "no ssh login after $1 s, see $log"
}

# wait_exit PATTERN: QEMU ends (reboot/poweroff) and the serial log contains PATTERN
wait_exit() {
	local i
	for ((i = 0; i < 60; i++)); do
		kill -0 $qpid 2>/dev/null || break
		sleep 1
	done
	kill -0 $qpid 2>/dev/null && die "QEMU still running 60 s after: $1"
	wait $qpid || true
	qpid=
	grep -q "$1" "$log" || die "\"$1\" missing in $log"
}

ok() { echo "  ok: $*" >&2; }

# screenshot NAME: the VM's screen as NAME.png in $out
screenshot() {
	rm -f "$out/$1.ppm"
	echo "screendump $out/$1.ppm" | socat - UNIX-CONNECT:"$out/monitor.sock" >/dev/null
	for _ in 1 2 3 4 5; do
		[[ -s $out/$1.ppm ]] && break
		sleep 1
	done
	magick "$out/$1.ppm" "$out/$1.png"
}

if [[ -n ${RESCUE_PHONE_BOOT:-} ]]; then
	msg "Boot 0: kernel and vendor ramdisk of the phone"
	[[ -f $RESCUE_PHONE_BOOT && -f ${RESCUE_PHONE_VENDOR_BOOT:-} ]] ||
		die "RESCUE_PHONE_BOOT and RESCUE_PHONE_VENDOR_BOOT must be files"
	# Kernel (gzip in boot.img v3/v4 of UT, lz4 legacy in that of Volla OS; QEMU needs it unpacked)
	# and the first vendor ramdisk (vendor_boot v4), as the bootloader loads them
	python3 -I -c '
import gzip, struct, subprocess, sys
b = open(sys.argv[1], "rb").read()
k = b[4096:4096 + struct.unpack_from("<I", b, 8)[0]]
if k[:2] == b"\x1f\x8b":
    k = gzip.decompress(k)
elif k[:4] == b"\x02\x21\x4c\x18":
    k = subprocess.run(["lz4", "-dc"], input=k, capture_output=True, check=True).stdout
open(sys.argv[3], "wb").write(k)
v = open(sys.argv[2], "rb").read()
u = lambda o: struct.unpack_from("<I", v, o)[0]
page, size = u(12), u(24)
start = -(-u(2096) // page) * page
table = start + -(-size // page) * page + -(-u(2100) // page) * page
first, offset = struct.unpack_from("<II", v, table)
open(sys.argv[4], "wb").write(v[start + offset:start + offset + first])
' "$RESCUE_PHONE_BOOT" "$RESCUE_PHONE_VENDOR_BOOT" "$out/phone-Image" "$out/phone-vendor"
	cat "$out/phone-vendor" "$out/rescue.cpio" > "$out/phone-initrd"
	log=$out/serial-0.log
	qemu-system-aarch64 -machine virt,gic-version=3 -cpu max,pauth-impdef=on -smp 2 -m 2048 -display none \
		-kernel "$out/phone-Image" -initrd "$out/phone-initrd" -append console=ttyAMA0 \
		-serial file:"$log" -no-reboot &
	qpid=$!
	for ((i = 0; i < 90; i++)); do
		grep -q "plinius-rescue: start" "$log" 2>/dev/null && break
		grep -q "Kernel panic" "$log" 2>/dev/null && break
		sleep 1
	done
	kill $qpid 2>/dev/null || true
	wait $qpid 2>/dev/null || true
	qpid=
	grep -q "plinius-rescue: start" "$log" || die "the phone's kernel did not start /init, see $log"
	ok "the phone's kernel unpacks vendor ramdisk + rescue ramdisk + key archive and starts /init"
	devfs=$(sed -n 's/.*plinius-rescue: start (\/dev: \(.*\))/\1/p' "$log")
	[[ -n $devfs ]] || die "/dev setup not logged, see $log"
	ok "/dev of the phone's kernel: $devfs"
fi

msg "Boot 1 (without devtmpfs): modules, partitions, read/write, dropbear restart, plinius-reboot bootloader"
start_vm 1 PLINIUS_RESCUE_NODEVTMPFS=1
wait_ssh 300
on_phone grep -qx ID=plinius-rescue /etc/os-release && ok "os-release"
[[ $(on_phone cat /etc/plinius-rescue-serial) == "$serial" ]] && ok "serial number from the second archive"
on_phone 'for i in $(seq 40); do [ -e /run/plinius-rescue-starting ] || exit 0; sleep 1; done; exit 1' ||
	die "/run/plinius-rescue-starting not removed"
ok "start marker removed"
rlog=$(on_phone cat /run/plinius-rescue.log)
grep -q "start (/dev: mdev)" <<< "$rlog" || die "/dev not from mdev: $rlog"
ok "/dev on tmpfs from mdev"
grep -q "modules.load.recovery pass 1: 1 loaded" <<< "$rlog" || die "recovery list not used first: $rlog"
grep -q "modules.load.rest pass 2: 2 loaded" <<< "$rlog" || die "rest of modules.load wrong: $rlog"
[[ $(grep "not loaded:" <<< "$rlog" | tail -n 1) == *"not loaded: missing.ko" ]] ||
	die "missing module not reported: $rlog"
ok "recovery list first, then the rest of modules.load with retries, missing.ko reported"
kmsg=$(on_phone dmesg)
grep -q "plinius-rescue: insmod failover.ko (pass 1)" <<< "$kmsg" && ok "module log in dmesg"
[[ $(grep -c "plinius-rescue: insmod virtio_mmio.ko" <<< "$kmsg") == 1 ]] ||
	die "virtio_mmio (in both lists) not loaded exactly once"
ok "a module in both lists is loaded once"

# The screen: plinius-splash reports the first picture, the status line (uptime) then changes
on_phone 'for i in $(seq 30); do dmesg | grep -q "plinius-splash: picture shown" && exit 0; sleep 1; done; exit 1' ||
	die "no picture on the screen: $(on_phone 'dmesg | grep -E "splash|display"; grep display /run/plinius-rescue.log')"
ok "plinius-splash: $(on_phone dmesg | sed -n 's/.*plinius-splash: \([0-9]*x[0-9]*, scale [0-9]*\).*/\1/p')"
screenshot screen-1
sleep 2
screenshot screen-2
python3 -I -c '
import sys
def pixels(path):
    d = open(path, "rb").read()
    # binary PPM: P6, width, height, maxval, each separated by whitespace, then RGB
    head = d.split(maxsplit=4)
    return d[len(d) - int(head[1]) * int(head[2]) * 3:]
a, b = pixels(sys.argv[1]), pixels(sys.argv[2])
# The accent colour from the second archive, without one Arch blue
color = bytes.fromhex(sys.argv[3] or "1793d1")
logo = sum(1 for i in range(0, len(a), 3) if a[i:i + 3] == color)
assert logo > 1000, f"logo missing ({logo} pixels of #{color.hex()})"
assert a != b, "the screen did not change in 2 s"
' "$out/screen-1.ppm" "$out/screen-2.ppm" "$accent_hex" || die "screen wrong, see $out/screen-1.png"
ok "logo in #${accent_hex:-1793d1} on the screen and the status line changes (screen-1.png, screen-2.png)"

list=$(on_phone sh < "$out/list.sh")
for p in "nvdata.*$((1 << 20))" "boot_a.*$((4 << 20))" "super.*$((8 << 20))"; do
	grep -qP "^P\t$p" <<< "$list" || die "partition list lacks $p: $list"
done
ok "partition list of the app (nvdata, boot_a, super)"
[[ $(on_phone head -c 1048576 /dev/disk/by-partlabel/nvdata | sha256sum) == "$(sha256sum < "$out/nvdata")" ]] ||
	die "nvdata reads back wrong"
ok "reading nvdata"
on_phone "sh -c 'cat > /dev/disk/by-partlabel/boot_a && sync'" < "$out/boot_a"
ok "writing boot_a (checked after the VM has ended)"

# System detection of the app: os-release read through the absolute link, unmounted afterwards,
# the partition unchanged (checked after the VM has ended)
systems=$( (echo "set -- /dev/disk/by-partlabel/linux"; cat "$out/systems.sh") | on_phone sh)
grep -qP '^/dev/disk/by-partlabel/linux\tIMAGE_VERSION=2026.11.02$' <<< "$systems" ||
	die "os-release of the linux partition not found: $systems"
on_phone grep -q plinius-systems /proc/mounts && die "linux partition still mounted"
ok "os-release of a Linux partition, unmounted afterwards"
# Tools for repartitioning the memory card, run read-only here (the partition stays unchanged)
on_phone 'e2fsck -fn /dev/disk/by-partlabel/linux >/dev/null && resize2fs -P /dev/disk/by-partlabel/linux 2>&1 |
	grep -q "minimum size" && sgdisk -p /dev/vda | grep -q linux && mkfs.ext4 -V 2>&1 | grep -q "^mke2fs 1\."' ||
	die "e2fsck, resize2fs, sgdisk or mkfs.ext4 missing or broken in the rescue system"
ok "e2fsck, resize2fs, sgdisk and mkfs.ext4 work"

# Tools archive through the app, as after its login; the second call finds the stamp and sends nothing
tools_py='
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("plinius_boot", sys.argv[1])
app = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(app)
access = app.SshAccess("127.0.0.1", "root", int(sys.argv[2]))
access.root = ""
sent = []
real_run = app.run
app.run = lambda cmd, input=None, **kw: (sent.append(len(input or b"")), real_run(cmd, input=input, **kw))[1]
ok = app.install_rescue_tools(access)
print(ok, max(sent))
'
res=$(XDG_CONFIG_HOME=$out/config python3 -I -c "$tools_py" "$app" $port)
[[ $res == "True "[1-9]* ]] || die "the app did not install the tools: $res"
res=$(XDG_CONFIG_HOME=$out/config python3 -I -c "$tools_py" "$app" $port)
[[ $res == "True "[0-9] || $res == "True "[0-9][0-9] ]] || die "the app sent the tools again: $res"
ok "tools archive installed by the app, sent once only"
on_phone 'cryptsetup --version | grep -q "^cryptsetup 2\." && cryptsetup isLuks /dev/disk/by-partlabel/userdata &&
	printf plinius-test | cryptsetup open --test-passphrase --key-file - /dev/disk/by-partlabel/userdata &&
	! printf wrong | cryptsetup open --test-passphrase --key-file - /dev/disk/by-partlabel/userdata 2>/dev/null' ||
	die "cryptsetup does not work in the rescue system"
ok "cryptsetup checks the passphrase of a LUKS2 partition"
on_phone 'dmsetup version | grep -q "^Driver version"' || die "dmsetup does not reach the device mapper"
ok "dmsetup reaches the device mapper without udev"

on_phone killall dropbear || true
sleep 1
wait_ssh 30
on_phone "grep -q 'dropbear exited' /run/plinius-rescue.log" && ok "dropbear restarted after being killed"

# The app's command for "Restart into bootloader" (SshAccess.reboot)
on_phone 'if command -v plinius-reboot >/dev/null; then plinius-reboot bootloader; else exit 9; fi' || true
wait_exit "Restarting system with command 'bootloader'"
ok "plinius-reboot bootloader"
cmp -n 65536 "$out/boot_a" <(dd if="$out/disk.img" bs=512 skip=4096 count=128 status=none) ||
	die "boot_a was not written"
ok "boot_a written to the disk"
cmp "$out/linux.img" <(dd if="$out/disk.img" bs=512 skip=28672 count=16384 status=none) ||
	die "the system detection changed the linux partition"
ok "linux partition unchanged by the system detection"

msg "Boot 2 (devtmpfs): busybox reboot (signal to PID 1)"
start_vm 2
wait_ssh 300
on_phone grep -q '"start (/dev: devtmpfs)"' /run/plinius-rescue.log || die "/dev not from devtmpfs"
ok "/dev from devtmpfs"
on_phone reboot || true
wait_exit "plinius-rescue: reboot"
ok "reboot"

msg "Boot 3: busybox poweroff"
start_vm 3
wait_ssh 300
on_phone poweroff || true
wait_exit "reboot: Power down"
ok "poweroff"

msg "All rescue system checks passed"
