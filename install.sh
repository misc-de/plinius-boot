#!/bin/bash
# Installs Plinius Boot for the current user into ~/.local, without root. Nothing outside the
# prefix changes, and the repository can be deleted afterwards. Run it again to update.
#
#   ./install.sh                  the app, with the rescue system if it is built
#   ./install.sh --build-rescue   builds the rescue system first (about 2 GB, takes a while)
#   PREFIX=/some/dir ./install.sh another place
#
# Everything it puts in place is listed in share/plinius-boot/installed-files, and uninstall.sh
# removes exactly that. Arch system-wide: makepkg -si. Sandboxed: see the Flatpak in README.md.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$here"

PREFIX=${PREFIX:-$HOME/.local}
BUILD=${BUILD:-$here/build}
ramdisk=$BUILD/rescue/plinius-rescue-aarch64.cpio.lz4
tools=$BUILD/rescue/plinius-rescue-tools-aarch64.tar.gz
own=share/plinius-boot
langs=(de es fr it nl pl pt ru)

die() { echo "Error: $*" >&2; exit 1; }

build_rescue=
case ${1:-} in
	"") ;;
	--build-rescue) build_rescue=1 ;;
	-h|--help) sed -n '2,9p' "$0" | sed 's/^# \?//'; exit 0 ;;
	*) die "unknown option $1, see --help" ;;
esac

[[ $(id -u) -ne 0 ]] || die "not as root, this installs for your own user (system-wide: makepkg -si)"

# Files must not change under a running app: it may be writing to a phone right now. Matches the
# interpreter running the app, not any command that merely mentions its name.
if pgrep -u "$(id -u)" -f '^[^ ]*python[0-9.]* ([^ ]*/)?plinius-boot( |$)' >/dev/null; then
	die "Plinius Boot is running, close it first (it may be writing to a phone)"
fi

# Missing programs are reported all at once, with the package to install
missing=()
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))' 2>/dev/null; then
	missing+=("Python 3.13 or later (python | python3)")
# Python 3.14 brings zstd, older ones take one of the two packages the app also knows
elif ! python3 -c 'import compression.zstd' 2>/dev/null && ! python3 -c 'from backports import zstd' 2>/dev/null &&
	! python3 -c 'import zstandard' 2>/dev/null; then
	missing+=("zstd for Python before 3.14 (python-zstandard | python3-zstandard)")
fi
python3 -c 'import gi, sys; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw; sys.exit(Adw.get_minor_version() < 6)' 2>/dev/null ||
	missing+=("GTK 4 and libadwaita 1.6 or later for Python (python-gobject gtk4 libadwaita | python3-gi gir1.2-adw-1)")
for need in ssh:openssh:openssh-client ssh-keygen:openssh:openssh-client adb:android-tools:adb \
	fastboot:android-tools:fastboot ip:iproute2:iproute2 msgfmt:gettext:gettext; do
	IFS=: read -r program arch debian <<< "$need"
	command -v "$program" >/dev/null || missing+=("$program ($arch | $debian)")
done
if ((${#missing[@]})); then
	echo "Missing, in parentheses the package on Arch/Manjaro | Debian/Ubuntu:" >&2
	printf '  %s\n' "${missing[@]}" >&2
	die "nothing was installed"
fi

if [[ -n $build_rescue ]]; then
	BUILD=$BUILD tools/chroot-init.sh
	BUILD=$BUILD rescue/mkrescue.sh
fi

# Everything is assembled in a temporary folder first, so that a failure changes nothing
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

install -Dm755 plinius-boot "$stage/bin/plinius-boot"
install -Dm644 LICENSE "$stage/share/licenses/plinius-boot/LICENSE"
install -Dm755 uninstall.sh "$stage/$own/uninstall.sh"
install -Dm644 org.plinius.Boot.svg "$stage/share/icons/hicolor/scalable/apps/org.plinius.Boot.svg"
for l in "${langs[@]}"; do
	mkdir -p "$stage/share/locale/$l/LC_MESSAGES"
	msgfmt --check -o "$stage/share/locale/$l/LC_MESSAGES/plinius-boot.mo" "po/$l.po"
done
[[ ! -f $ramdisk ]] || install -Dm644 "$ramdisk" "$stage/$own/$(basename "$ramdisk")"
[[ ! -f $tools ]] || install -Dm644 "$tools" "$stage/$own/$(basename "$tools")"

# With the full path: ~/.local/bin is not on the PATH of every desktop
desktop=$(<org.plinius.Boot.desktop)
mkdir -p "$stage/share/applications"
printf '%s\n' "${desktop/Exec=plinius-boot/Exec=\"$PREFIX/bin/plinius-boot\"}" \
	> "$stage/share/applications/org.plinius.Boot.desktop"
chmod 644 "$stage/share/applications/org.plinius.Boot.desktop"

# The list covers itself, so that the uninstaller also removes it
files=$(cd "$stage" && { find . -type f | sed 's|^\./||'; echo "$own/installed-files"; } | LC_ALL=C sort -u)
printf '%s\n' "$files" > "$stage/$own/installed-files"

# Updating: what the previous version installed and this one does not goes away. Only plain
# relative paths count, a damaged list must not reach outside the prefix.
if [[ -f $PREFIX/$own/installed-files ]]; then
	while IFS= read -r f; do
		case $f in /*|*..*|"") continue ;; esac
		grep -qxF -- "$f" "$stage/$own/installed-files" || rm -f -- "${PREFIX:?}/$f"
	done < "$PREFIX/$own/installed-files"
fi

mkdir -p "$PREFIX"
cp -RT "$stage" "$PREFIX"
update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" 2>/dev/null || true

echo "Plinius Boot is installed in $PREFIX. Start it from the menu or with: plinius-boot"
case :$PATH: in
	*:"$PREFIX/bin":*) ;;
	*) echo "Note: $PREFIX/bin is not on your PATH, the menu entry works anyway." ;;
esac
if [[ ! -f $ramdisk ]]; then
	echo "Note: the rescue system is not built, so the app cannot start it."
	echo "      Everything else works. To add it: ./install.sh --build-rescue"
fi
if ! ls /usr/lib/udev/rules.d/*android* /etc/udev/rules.d/*android* >/dev/null 2>&1; then
	echo "Note: no udev rules for Android devices were found. adb and fastboot may not be allowed"
	echo "      to reach the phone (Arch/Manjaro: package android-udev)."
fi
if command -v pacman >/dev/null && pacman -Q plinius-boot >/dev/null 2>&1; then
	echo "Note: the pacman package plinius-boot is installed as well, you only need one of them."
fi
