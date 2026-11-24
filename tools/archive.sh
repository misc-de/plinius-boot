#!/bin/bash
# Packs the app with its rescue system for use without Flatpak: build/release/plinius-boot.tar.gz and .zip,
# the same content in both, a folder plinius-boot-<version>. Without the version in their names, so that the
# link to the latest release stays the same. Unpacked, ./plinius-boot starts the app right there and
# ./install.sh installs it for the user.
#
#   tools/archive.sh
#
# What is committed goes in (git archive of HEAD), uncommitted changes do not. The rescue ramdisk and
# its tools go to build/rescue/ in the archive, where the app and install.sh look for them, so they
# must be built first (rescue/mkrescue.sh). BUILD chooses another build folder, as in install.sh.
# tools/flatpak.sh --release publishes the archives along with the Flatpaks.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

BUILD=${BUILD:-$root/build}
rescue=(plinius-rescue-aarch64.cpio.lz4 plinius-rescue-tools-aarch64.tar.gz)

die() { echo "Error: $*" >&2; exit 1; }
msg() { echo "==> $*" >&2; }

case ${1:-} in
	"") ;;
	-h|--help) sed -n '2,12p' "$0" | sed 's/^# \?//'; exit 0 ;;
	*) die "unknown option $1, see --help" ;;
esac

version=$(git show HEAD:plinius-boot | sed -n 's/^VERSION = "\(.*\)"$/\1/p')
[[ -n $version ]] || die "no VERSION in the app"
for f in "${rescue[@]}"; do
	[[ -f $BUILD/rescue/$f ]] || die "no $f in $BUILD/rescue, run tools/chroot-init.sh and rescue/mkrescue.sh first"
done
[[ -z $(git status --porcelain) ]] || msg "Note: uncommitted changes are not in the archive, it holds HEAD"

name=plinius-boot-$version
out=$BUILD/release
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

git archive --format=tar --prefix="$name/" HEAD | tar -x -C "$stage"
for f in "${rescue[@]}"; do
	install -Dm644 "$BUILD/rescue/$f" "$stage/$name/build/rescue/$f"
done

# Same owner and time for every file, the time of the commit
stamp=$(git log -1 --format=%ct HEAD)
find "$stage/$name" -exec touch -h -d "@$stamp" {} +
mkdir -p "$out"
rm -f "$out/plinius-boot.tar.gz" "$out/plinius-boot.zip"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@$stamp" -C "$stage" -czf "$out/plinius-boot.tar.gz" "$name"
# zip keeps the execute bits, unzip and the file managers restore them
(cd "$stage" && find "$name" -print | LC_ALL=C sort | zip -q -X -@ "$out/plinius-boot.zip")
msg "Done: $out/plinius-boot.tar.gz and .zip with $name ($(du -h "$out/plinius-boot.tar.gz" | cut -f1))"
printf '%s\n' "$out/plinius-boot.tar.gz" "$out/plinius-boot.zip"
