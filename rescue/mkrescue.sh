#!/bin/bash
# Builds the ramdisk of the rescue system for plinius-boot (docs/SICHERUNG.md#rettungssystem).
# The app puts it as the ramdisk into the phone's boot image and starts that with
# "fastboot boot", i.e. in RAM only. Contents: busybox (static), dropbear with its libraries,
# plinius-reboot (rescue/plinius-reboot.c), plinius-splash (rescue/plinius-splash.c) with a Terminus
# console font (OFL), e2fsprogs (e2fsck, resize2fs, mke2fs) and sgdisk for repartitioning the
# memory card, and rescue/rescue-init as /init. The app appends the SSH key as a second cpio archive.
#
# Next to it: plinius-rescue-tools-aarch64.tar.gz with cryptsetup and dmsetup, their own loader and
# libraries under /opt/plinius-tools and callers in /bin. They do not fit into the ramdisk: the
# bootloader loads at most 22,020,096 bytes of ramdisk, vendor ramdisk included, and OpenSSL alone
# would take up most of what is left. plinius-boot unpacks the archive into a running rescue system
# over SSH (see install_rescue_tools in the app).
#
# Usage: rescue/mkrescue.sh [-o output.cpio.lz4]
#   default output: build/rescue/plinius-rescue-aarch64.cpio.lz4 (where plinius-boot looks), the
#   tools archive in the same folder
set -euo pipefail
. "$(dirname "$0")/../tools/lib.sh"

out=$BUILD/rescue/plinius-rescue-aarch64.cpio.lz4
while getopts o: opt; do
	case $opt in
		o) out=$(realpath -m "$OPTARG") ;;
		*) exit 2 ;;
	esac
done
[[ -d $CHROOT/usr ]] || die "build chroot missing, run tools/chroot-init.sh first"
check_host

work=$(mktemp -d "$BUILD/work-rescue.XXXXXX")
trap 'ns_rm "$work"' EXIT
tools=$(dirname "$out")/plinius-rescue-tools-aarch64.tar.gz
mkdir -p "$work"/{upper,ovl,merged} "$(dirname "$out")"

msg "Building rescue system"
ns_run '
	lower=$1 work=$2 init=$3 out=$4 reboot_c=$5 splash_c=$6 tools=$7
	r=$work/merged
	# userxattr: in an unprivileged overlay, rmdir on a lower directory otherwise fails with EIO
	# and pacman -Syu leaves a broken database behind
	mount -t overlay overlay -o "lowerdir=$lower,upperdir=$work/upper,workdir=$work/ovl,userxattr" "$r"
	chroot_mounts "$r"
	# -Syu: dropbear and the glibc copied along must be from the same state
	in_chroot "$r" pacman -Syu --noconfirm --needed busybox dropbear cpio gcc terminus-font e2fsprogs gptfdisk \
		cryptsetup device-mapper >/dev/null

	# Real directories instead of symlinks (/lib -> usr/lib): the vendor ramdisk brings a real
	# /lib/modules, and a symlink could not be laid over it when unpacking.
	t=$r/tmp/rescue
	mkdir -p "$t"/{bin,lib,usr/lib,usr/share/plinius-rescue,etc,root,proc,sys,dev,tmp,run}
	cp "$r/usr/bin/busybox" "$t/bin/"
	# Dynamically linked, their libraries are copied below
	bins="dropbear e2fsck resize2fs mke2fs tune2fs dumpe2fs sgdisk"
	for b in $bins; do
		cp "$r/usr/bin/$b" "$t/bin/"
	done
	ln -s mke2fs "$t/bin/mkfs.ext4"
	cp "$r/etc/mke2fs.conf" "$t/etc/"
	# Linked against the same glibc as dropbear (copied below)
	cp "$reboot_c" "$r/tmp/plinius-reboot.c"
	in_chroot "$r" gcc -O2 -s -Wall -Werror -o /tmp/rescue/bin/plinius-reboot /tmp/plinius-reboot.c
	cp "$splash_c" "$r/tmp/plinius-splash.c"
	in_chroot "$r" gcc -O2 -s -Wall -Wextra -Werror -o /tmp/rescue/bin/plinius-splash /tmp/plinius-splash.c
	# 8x16 bold, scaled up by plinius-splash, which reads it unpacked only
	gzip -dc "$r/usr/share/kbd/consolefonts/ter-v16b.psf.gz" > "$t/usr/share/plinius-rescue/font.psf"
	# As an assignment, so that set -e does not ignore a failure of ldd
	libs=
	for b in $bins; do
		found=$(in_chroot "$r" ldd "/usr/bin/$b" | grep -o "/[^ ]*")
		[[ -n $found ]] || { echo "ldd reports no libraries for $b" >&2; exit 1; }
		libs="$libs $found"
	done
	for lib in $(printf "%s\n" $libs | sort -u); do
		case $lib in
			*/ld-linux-*) cp -L "$r$lib" "$t/lib/" ;;
			*) cp -L "$r$lib" "$t/usr/lib/" ;;
		esac
	done
	install -m755 "$init" "$t/init"
	echo "root:*:0:0:root:/root:/bin/sh" > "$t/etc/passwd"
	echo "root:x:0:" > "$t/etc/group"
	echo /bin/sh > "$t/etc/shells"
	printf "%s\n" "NAME=\"Plinius rescue\"" "ID=plinius-rescue" "PRETTY_NAME=\"Plinius rescue system\"" \
		> "$t/etc/os-release"
	chmod 700 "$t/root"

	# Smoke test in the finished tree: busybox runs, /init parses (a syntax error would end
	# PID 1 only when the shell reaches it), dropbear and plinius-reboot find their libraries.
	# plinius-reboot itself must not run here: it would end the build namespace.
	chroot "$t" /bin/busybox true
	chroot "$t" /bin/busybox sh -n /init
	chroot "$t" /lib/ld-linux-aarch64.so.1 --list /bin/plinius-reboot >/dev/null
	chroot "$t" /lib/ld-linux-aarch64.so.1 --list /bin/plinius-splash >/dev/null
	for b in $bins; do
		chroot "$t" /lib/ld-linux-aarch64.so.1 --list "/bin/$b" >/dev/null ||
			{ echo "$b does not find its libraries in the rescue system" >&2; exit 1; }
	done
	chroot "$t" /bin/dropbear -V 2>&1 | grep -q "^Dropbear" ||
		{ echo "dropbear does not start in the rescue system" >&2; exit 1; }

	# newc cpio owned by root and sorted, compressed as LZ4 legacy like the vendor ramdisk. The
	# bootloader puts the vendor ramdisk directly in front of this one, and the kernel cannot find
	# the end of an LZ4 legacy stream: the next archive must be LZ4 legacy as well (it continues at
	# the magic number). Anything else fails with "Decoding failed" and a kernel panic (tested with
	# the Plinius kernel and its vendor ramdisk in QEMU). plinius-boot appends its archive the same way.
	in_chroot "$r" sh -c "cd /tmp/rescue && find . | LC_ALL=C sort | cpio -o -H newc -R 0:0 --quiet" |
		lz4 -l -9 -q > "$out"
	ls -l "$t/bin" "$t/lib" "$t/usr/lib" >&2

	# Tools archive: with their own loader and glibc, so they run whatever rescue ramdisk is on
	# the phone. The rescue system has no udev, so libdevmapper must not wait for it.
	tt=$r/tmp/tools d=/opt/plinius-tools
	mkdir -p "$tt/bin" "$tt$d/bin" "$tt$d/lib"
	libs=
	for b in cryptsetup dmsetup; do
		cp "$r/usr/bin/$b" "$tt$d/bin/"
		found=$(in_chroot "$r" ldd "/usr/bin/$b" | grep -o "/[^ ]*")
		[[ -n $found ]] || { echo "ldd reports no libraries for $b" >&2; exit 1; }
		libs="$libs $found"
		printf "%s\n" "#!/bin/sh" "# From plinius-boot (rescue/mkrescue.sh): own loader and libraries, no udev" \
			"DM_DISABLE_UDEV=1 exec $d/lib/ld-linux-aarch64.so.1 --library-path $d/lib $d/bin/$b \"\$@\"" \
			> "$tt/bin/$b"
		chmod 755 "$tt/bin/$b"
	done
	for lib in $(printf "%s\n" $libs | sort -u); do
		cp -L "$r$lib" "$tt$d/lib/"
	done
	# dmsetup --version also asks the kernel driver, which a chroot without /dev does not have
	for b in cryptsetup dmsetup; do
		chroot "$tt" $d/lib/ld-linux-aarch64.so.1 --library-path $d/lib --list $d/bin/$b >/dev/null ||
			{ echo "$b does not find its libraries in the tools archive" >&2; exit 1; }
	done
	chroot "$tt" $d/lib/ld-linux-aarch64.so.1 --library-path $d/lib $d/bin/cryptsetup --version >/dev/null ||
		{ echo "cryptsetup does not start from the tools archive" >&2; exit 1; }
	in_chroot "$r" tar -C /tmp/tools --owner=0 --group=0 --numeric-owner --sort=name -czf - bin opt > "$tools"
	ls -l "$tt$d/lib" >&2
' "$CHROOT" "$work" "$(realpath "$(dirname "$0")/rescue-init")" "$out" \
	"$(realpath "$(dirname "$0")/plinius-reboot.c")" "$(realpath "$(dirname "$0")/plinius-splash.c")" "$tools"

msg "Done: $out ($(du -h "$out" | cut -f1)), $tools ($(du -h "$tools" | cut -f1))"
