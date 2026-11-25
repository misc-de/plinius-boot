# Maintainer: misc-de <misc-de@users.noreply.github.com>

pkgname=plinius-boot
pkgver=0.31
pkgrel=1
pkgdesc="Back up and flash Volla and FuriLabs phones, with a rescue system (runs on the PC)"
arch=(any)
url="https://github.com/misc-de/plinius-boot"
# Own app MIT; the licenses of what the rescue system ships are listed in README.md
license=(MIT)
# For the PC, not for the phone. compression.zstd requires Python 3.14 or later.
# Phones over USB: ssh (Linux on the phone), adb (Android with root), fastboot (restore only).
depends=('python>=3.14' python-gobject gtk4 libadwaita openssh android-tools iproute2)
optdepends=('android-udev: USB access to phones in adb and fastboot mode')
makedepends=(gettext)
_langs=(de es fr it nl pl pt ru)

# Packs the working tree it lives in, there are no sources to fetch. The rescue ramdisk comes from
# rescue/mkrescue.sh; it is not reproducible (timestamps in the cpio), and it stays out of git.
_ramdisk=plinius-rescue-aarch64.cpio.lz4
_tools=plinius-rescue-tools-aarch64.tar.gz

build() {
	[[ -f $startdir/build/rescue/$_ramdisk ]] ||
		{ error "no rescue ramdisk, run tools/chroot-init.sh and rescue/mkrescue.sh first"; return 1; }
	[[ -f $startdir/build/rescue/$_tools ]] ||
		{ error "no rescue tools archive, run rescue/mkrescue.sh again"; return 1; }
	for l in "${_langs[@]}"; do
		msgfmt --check -o "$srcdir/$l.mo" "$startdir/po/$l.po"
	done
}

package() {
	cd "$startdir"
	install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/$pkgname"
	install -Dm755 plinius-boot -t "$pkgdir/usr/bin"
	install -Dm644 org.plinius.Boot.desktop -t "$pkgdir/usr/share/applications"
	install -Dm644 org.plinius.Boot.svg -t "$pkgdir/usr/share/icons/hicolor/scalable/apps"
	install -Dm644 "build/rescue/$_ramdisk" "build/rescue/$_tools" -t "$pkgdir/usr/share/plinius-boot"
	for l in "${_langs[@]}"; do
		install -Dm644 "$srcdir/$l.mo" "$pkgdir/usr/share/locale/$l/LC_MESSAGES/plinius-boot.mo"
	done
}
