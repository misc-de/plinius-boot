#!/bin/bash
# Builds the Flatpak for x86_64 and aarch64, and offers it as a release on GitHub. Every version of the
# app gets one.
#
#   tools/flatpak.sh                  builds build/flatpak/org.plinius.Boot-<arch>.flatpak for both
#   tools/flatpak.sh --arch x86_64    only this architecture
#   tools/flatpak.sh --release        also publishes them as the release of the current version, together
#                                     with the archives of tools/archive.sh (for use without Flatpak);
#                                     refuses unless everything is committed and pushed
#
# The first build takes a while (protobuf and android-tools from source), later ones reuse them. aarch64
# runs under qemu-user (package qemu-user-static-binfmt) and is much slower. The rescue ramdisk goes into
# the Flatpak, so it must be built first (rescue/mkrescue.sh).
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

id=org.plinius.Boot
out=build/flatpak
arches=(x86_64 aarch64)

die() { echo "Error: $*" >&2; exit 1; }
msg() { echo "==> $*" >&2; }

release=
while (($#)); do
	case $1 in
		--release) release=1 ;;
		--arch)
			[[ ${2:-} == x86_64 || ${2:-} == aarch64 ]] || die "--arch takes x86_64 or aarch64"
			arches=("$2")
			shift
			;;
		-h|--help) sed -n '2,13p' "$0" | sed 's/^# \?//'; exit 0 ;;
		*) die "unknown option $1, see --help" ;;
	esac
	shift
done

version=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' plinius-boot)
[[ -n $version ]] || die "no VERSION in the app"
# One version everywhere, or the release would contradict itself
[[ $(sed -n 's/^pkgver=//p' PKGBUILD) == "$version" ]] || die "PKGBUILD has another version than the app ($version)"
grep -q "<release version=\"$version\"" "$id.metainfo.xml" || die "no release $version in $id.metainfo.xml"

[[ -f build/rescue/plinius-rescue-aarch64.cpio.lz4 ]] ||
	die "no rescue ramdisk, run tools/chroot-init.sh and rescue/mkrescue.sh first"
[[ -f build/rescue/plinius-rescue-tools-aarch64.tar.gz ]] ||
	die "no rescue tools archive, run rescue/mkrescue.sh again"

for arch in "${arches[@]}"; do
	[[ $arch == "$(flatpak --default-arch)" ]] || [[ -r /proc/sys/fs/binfmt_misc/qemu-$arch ]] ||
		die "building for $arch here needs qemu-$arch binfmt (package qemu-user-static-binfmt)"
done

if [[ -n $release ]]; then
	[[ -z $(git status --porcelain) ]] || die "uncommitted changes, a release must match its commit"
	git fetch -q origin
	[[ $(git rev-parse HEAD) == "$(git rev-parse origin/main)" ]] || die "HEAD is not what is on origin/main"
fi

mkdir -p "$out"
bundles=()
for arch in "${arches[@]}"; do
	msg "Building $id $version for $arch"
	flatpak-builder --force-clean --user --install-deps-from=flathub --disable-updates --arch="$arch" \
		--state-dir="$out/state" --repo="$out/repo" "$out/app-$arch" "$id.yaml"
	bundle=$out/$id-$arch.flatpak
	msg "Packing $bundle"
	flatpak build-bundle --arch="$arch" --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
		"$out/repo" "$bundle" "$id"
	msg "Done: $bundle ($(du -h "$bundle" | cut -f1))"
	bundles+=("$bundle")
done

if [[ -n $release ]]; then
	# Not through <(...): a failure there would not stop the release
	packed=$(tools/archive.sh)
	mapfile -t archives <<< "$packed"
	tag=v$version
	if gh release view "$tag" >/dev/null 2>&1; then
		msg "Replacing the Flatpaks and archives of release $tag"
		gh release upload "$tag" "${bundles[@]}" "${archives[@]}" --clobber
	else
		msg "Creating release $tag"
		gh release create "$tag" "${bundles[@]}" "${archives[@]}" --target "$(git rev-parse HEAD)" \
			--title "Plinius Boot $version" --notes "Flatpak: install the file for your architecture with
flatpak install --user $id-<arch>.flatpak

Without Flatpak: unpack plinius-boot.tar.gz or .zip, then start ./plinius-boot in its folder, or
install it for your user with ./install.sh. It names whatever is missing on your system."
	fi
fi
