#!/bin/bash
# Creates the aarch64 build chroot of the rescue system or brings it up to date: the Arch Linux ARM
# tarball, run through qemu-user. Idempotent: an existing chroot is only updated.
#
# Usage: tools/chroot-init.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"

check_host

if [[ ! -d $CHROOT/usr ]]; then
	mkdir -p "$DOWNLOADS" "$CHROOT"
	tarball=ArchLinuxARM-aarch64-latest.tar.gz
	url=$MIRROR/os/$tarball
	if [[ ! -f $DOWNLOADS/$tarball ]]; then
		msg "Downloading $tarball"
		curl -fL -o "$DOWNLOADS/$tarball.part" "$url"
		curl -fsL -o "$DOWNLOADS/$tarball.md5" "$url.md5"
		mv "$DOWNLOADS/$tarball.part" "$DOWNLOADS/$tarball"
	fi
	[[ -f $DOWNLOADS/$tarball.sig ]] || curl -fsL -o "$DOWNLOADS/$tarball.sig" "$url.sig"
	(cd "$DOWNLOADS" && md5sum -c --quiet "$tarball.md5") ||
		die "checksum of $tarball does not match"
	# The checksum comes from the same server and only verifies the transfer. The tarball is
	# authentic only with a valid signature; it also supplies the keyring pacman trusts afterwards.
	# Arch Linux ARM Build System <builder@archlinuxarm.org>
	fpr=68B3537F39A313B3E574D06777193F152BDBE6A6
	keyring=$DOWNLOADS/archlinuxarm-builder.gpg
	if [[ ! -s $keyring ]]; then
		gnupg=$(mktemp -d)
		gpg --homedir "$gnupg" --quiet --keyserver hkps://keyserver.ubuntu.com --recv-keys "$fpr"
		gpg --homedir "$gnupg" --export "$fpr" > "$keyring"
		gpgconf --homedir "$gnupg" --kill all
		rm -rf "$gnupg"
	fi
	# gpgv is not on every build host; gpg --verify with a throwaway home checks the same keyring
	if command -v gpgv >/dev/null; then
		verify=(gpgv --keyring "$keyring")
	else
		vhome=$(mktemp -d)
		verify=(gpg --homedir "$vhome" --batch --no-default-keyring --keyring "$keyring" --verify)
	fi
	"${verify[@]}" "$DOWNLOADS/$tarball.sig" "$DOWNLOADS/$tarball" 2>/dev/null ||
		die "signature of $tarball missing or invalid"
	[[ -z ${vhome:-} ]] || { gpgconf --homedir "$vhome" --kill all; rm -rf "$vhome"; }
	msg "Extracting to $CHROOT"
	unshare --map-auto --map-root-user bsdtar -xpf "$DOWNLOADS/$tarball" -C "$CHROOT"
fi

msg "Configuring and updating the chroot (takes a while under qemu)"
ns_run '
	r=$1 keyring=$2 mirrorlist=$3
	# Landlock and switching to the alpm user do not work under qemu-user.
	# Applies only to this build chroot, not to the phone.
	grep -q "^DisableSandbox" "$r/etc/pacman.conf" ||
		sed -i "s/^\[options\]/[options]\nDisableSandbox/" "$r/etc/pacman.conf"
	# Up to 10 downloads at once instead of the default 5
	sed -i "s/^#\?ParallelDownloads.*/ParallelDownloads = 10/" "$r/etc/pacman.conf"
	printf "%s\n" "$mirrorlist" > "$r/etc/pacman.d/mirrorlist"

	chroot_mounts "$r"
	if [[ ! -f $r/etc/pacman.d/gnupg/trustdb.gpg ]]; then
		in_chroot "$r" pacman-key --init
		in_chroot "$r" pacman-key --populate "$keyring"
	fi
	# A build chroot needs no kernel or firmware; they only make every update longer.
	for p in linux-aarch64 linux-firmware; do
		in_chroot "$r" pacman -Q "$p" >/dev/null 2>&1 && in_chroot "$r" pacman -Rns --noconfirm "$p"
	done
	in_chroot "$r" pacman -Syu --noconfirm
	# Only downloaded into the cache: rescue/test-rescue.sh boots this kernel in QEMU
	in_chroot "$r" pacman -Sw --noconfirm linux-aarch64
' "$CHROOT" "$KEYRING" "$(mirrorlist)"

msg "Build chroot ready: $CHROOT"
