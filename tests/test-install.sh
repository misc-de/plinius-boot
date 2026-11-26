#!/bin/bash
# Tests install.sh and uninstall.sh in throw-away folders: the real home is never touched, no phone
# is needed, and no window opens. Fails if Plinius Boot is running, because the scripts then refuse.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
sleeper=
cleanup() {
	[[ -z $sleeper ]] || kill "$sleeper" 2>/dev/null || true
	rm -rf "$tmp"
}
trap cleanup EXIT

ok() { echo "  ok: $*"; }
fail() { echo "FAILED: $*" >&2; exit 1; }

# A space in the path, as a trap for every unquoted variable
export HOME="$tmp/the home"
export XDG_CONFIG_HOME=$HOME/.config XDG_DATA_HOME=$HOME/.local/share XDG_CACHE_HOME=$HOME/.cache
export BUILD=$tmp/build
export PYTHONDONTWRITEBYTECODE=1
unset PREFIX
mkdir -p "$HOME" "$BUILD/rescue"
prefix=$HOME/.local
list=$prefix/share/plinius-boot/installed-files
ramdisk=$prefix/share/plinius-boot/plinius-rescue-aarch64.cpio.lz4
echo fake-ramdisk > "$BUILD/rescue/plinius-rescue-aarch64.cpio.lz4"

"$root/install.sh" > "$tmp/out"
ok "installed into a prefix with a space in its path"

# The list and the prefix must agree exactly: that is what makes uninstalling complete
# (mimeinfo.cache, and since desktop-file-utils 0.29 intent.cache, belong to update-desktop-database and
# are shared with every other application)
diff <(cd "$prefix" && find . -type f -not -name mimeinfo.cache -not -name intent.cache -not -name icon-theme.cache | sed 's|^\./||' | LC_ALL=C sort) "$list" ||
	fail "list and files differ"
ok "the list names exactly the installed files ($(wc -l < "$list"))"

[[ -x $prefix/bin/plinius-boot ]] || fail "app not executable"
[[ ! -L $prefix/bin/plinius-boot ]] || fail "the app must not depend on the repository"
grep -qxF "Exec=\"$prefix/bin/plinius-boot\"" "$prefix/share/applications/org.plinius.Boot.desktop" ||
	fail "desktop entry without the full path"
if command -v desktop-file-validate >/dev/null; then
	desktop-file-validate "$prefix/share/applications/org.plinius.Boot.desktop" || fail "desktop entry invalid"
fi
ok "the desktop entry is valid and has the full path"

# The installed app itself must find its data next to it, run from outside the repository
(cd "$tmp" && LANGUAGE=de LC_ALL=C.UTF-8 python3 -I -B -c '
import importlib.machinery, importlib.util, os, sys
loader = importlib.machinery.SourceFileLoader("plinius_boot", sys.argv[1])
app = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(app)
assert os.path.realpath(app.rescue_ramdisk()) == os.path.realpath(sys.argv[2]), app.rescue_ramdisk()
assert app._("About {name}") == "Über {name}", "no translation"
' "$prefix/bin/plinius-boot" "$ramdisk") || fail "the installed app does not find its data"
ok "the installed app finds its ramdisk and translations"

# Things that are not ours
mkdir -p "$prefix/bin" "$prefix/share/applications" "$prefix/share/locale/de/LC_MESSAGES"
echo mine > "$prefix/bin/other"
echo mine > "$prefix/share/applications/other.desktop"
echo mine > "$prefix/share/locale/de/LC_MESSAGES/other.mo"

# Updating: a file of the previous version disappears, others stay
echo old > "$prefix/share/plinius-boot/from-an-old-version"
echo share/plinius-boot/from-an-old-version >> "$list"
rm "$BUILD/rescue/plinius-rescue-aarch64.cpio.lz4"
"$root/install.sh" > "$tmp/out"
[[ ! -e $prefix/share/plinius-boot/from-an-old-version ]] || fail "update left an old file"
[[ ! -e $ramdisk ]] || fail "update kept the ramdisk it no longer has"
grep -q -- "--build-rescue" "$tmp/out" || fail "no hint about the missing rescue system"
[[ -f $prefix/bin/other ]] || fail "update removed a foreign file"
ok "an update removes only what the last version had"

# Refused while the app runs, with the same command line as a real start
mkdir -p "$tmp/fake"
printf '#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n' > "$tmp/fake/plinius-boot"
chmod +x "$tmp/fake/plinius-boot"
"$tmp/fake/plinius-boot" & sleeper=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
	[[ $(tr '\0' ' ' < "/proc/$sleeper/cmdline" 2>/dev/null) == *python* ]] && break
	sleep 0.2
done
if "$root/install.sh" > "$tmp/out" 2>&1; then fail "install.sh ran next to a running app"; fi
grep -q "running" "$tmp/out" || fail "install.sh failed for another reason: $(cat "$tmp/out")"
if "$root/uninstall.sh" > "$tmp/out" 2>&1; then fail "uninstall.sh ran next to a running app"; fi
grep -q "running" "$tmp/out" || fail "uninstall.sh failed for another reason: $(cat "$tmp/out")"
[[ -f $list ]] || fail "the refused uninstall removed something"
kill "$sleeper"; wait "$sleeper" 2>/dev/null || true; sleeper=
ok "both refuse while the app runs"

# A command that only mentions the name is no running app
(exec -a "bash plinius-boot" sleep 60) & sleeper=$!
"$root/install.sh" > "$tmp/out" || fail "a mere mention of the name blocked the installer"
kill "$sleeper"; wait "$sleeper" 2>/dev/null || true; sleeper=
ok "another command with the name in it does not count"

# Refused as root, tried in a user namespace
if unshare --map-root-user "$root/install.sh" > "$tmp/out" 2>&1; then fail "install.sh ran as root"; fi
grep -q "not as root" "$tmp/out" || fail "install.sh failed for another reason: $(cat "$tmp/out")"
if unshare --map-root-user "$root/uninstall.sh" > "$tmp/out" 2>&1; then fail "uninstall.sh ran as root"; fi
ok "both refuse root"

# A missing program is named, and nothing changes: PATH without adb and fastboot
mkdir "$tmp/path"
IFS=: read -ra dirs <<< "$PATH"
for d in "${dirs[@]}"; do
	[[ -d $d ]] || continue
	for f in "$d"/*; do
		case ${f##*/} in adb|fastboot) continue ;; esac
		[[ -x $f && ! -d $f && ! -e $tmp/path/${f##*/} ]] && ln -s "$f" "$tmp/path/${f##*/}"
	done
done
before=$(cd "$prefix" && find . -type f | LC_ALL=C sort | xargs -d '\n' sha256sum)
if PATH=$tmp/path "$root/install.sh" > "$tmp/out" 2>&1; then fail "installed without adb"; fi
grep -q "adb (android-tools | adb)" "$tmp/out" || fail "missing adb not reported: $(cat "$tmp/out")"
grep -q "fastboot (android-tools | fastboot)" "$tmp/out" || fail "missing fastboot not reported"
[[ $before == "$(cd "$prefix" && find . -type f | LC_ALL=C sort | xargs -d '\n' sha256sum)" ]] ||
	fail "a failed install changed files"
ok "missing programs are named and nothing changes"

# Python before 3.14 without any zstd module: named, with the package that brings it
mkdir "$tmp/nozstd"
printf '#!/bin/sh\ncase "$*" in *zstd*|*zstandard*) exit 1 ;; esac\nexec %s "$@"\n' "$(command -v python3)" \
	> "$tmp/nozstd/python3"
chmod +x "$tmp/nozstd/python3"
if PATH=$tmp/nozstd:$PATH "$root/install.sh" > "$tmp/out" 2>&1; then fail "installed without zstd"; fi
grep -q "zstd for Python before 3.14 (python-zstandard | python3-zstandard)" "$tmp/out" ||
	fail "missing zstd not reported: $(cat "$tmp/out")"
[[ $before == "$(cd "$prefix" && find . -type f | LC_ALL=C sort | xargs -d '\n' sha256sum)" ]] ||
	fail "a failed install changed files"
ok "a missing zstd module is named"

# The user's data, which must survive whatever happens
mkdir -p "$XDG_CONFIG_HOME/plinius-boot" "$XDG_DATA_HOME/plinius-boot/originals/SER" \
	"$HOME/Phone Backups/model/date" "$XDG_CACHE_HOME/plinius-boot"
echo key > "$XDG_CONFIG_HOME/plinius-boot/rescue_ed25519"
echo original > "$XDG_DATA_HOME/plinius-boot/originals/SER/boot.img"
echo backup > "$HOME/Phone Backups/model/date/boot.img.zst"
echo temporary > "$XDG_CACHE_HOME/plinius-boot/original.img"

# A damaged list must not reach outside the prefix
echo precious > "$tmp/outside"
printf '%s\n' "../../../outside" "$tmp/outside" >> "$list"

# The copy in the prefix works without the repository and finds the prefix by itself
(cd "$tmp" && "$prefix/share/plinius-boot/uninstall.sh") > "$tmp/out"
[[ -f $tmp/outside ]] || fail "a damaged list reached outside the prefix"
ok "a damaged list cannot reach outside the prefix"

left=$(cd "$prefix" && find . -type f -not -path './share/plinius-boot/originals/*' \
	-not -name other -not -name other.desktop -not -name other.mo -not -name mimeinfo.cache -not -name intent.cache -not -name icon-theme.cache)
[[ -z $left ]] || fail "left behind: $left"
for f in bin/other share/applications/other.desktop share/locale/de/LC_MESSAGES/other.mo; do
	[[ -f $prefix/$f ]] || fail "removed a foreign file: $f"
done
ok "the uninstaller removes all of its files and only those"

[[ $(cat "$XDG_CONFIG_HOME/plinius-boot/rescue_ed25519") == key ]] || fail "the key is gone"
[[ $(cat "$XDG_DATA_HOME/plinius-boot/originals/SER/boot.img") == original ]] || fail "the original is gone"
[[ $(cat "$HOME/Phone Backups/model/date/boot.img.zst") == backup ]] || fail "the backup is gone"
[[ ! -e $XDG_CACHE_HOME/plinius-boot ]] || fail "the cache is still there"
grep -q "rescue systems" "$tmp/out" || fail "does not say what it kept"
ok "backups, keys and originals stay, the cache goes"

if "$root/uninstall.sh" > "$tmp/out" 2>&1; then fail "uninstalled nothing successfully"; fi
grep -q "no installation found" "$tmp/out" || fail "wrong message: $(cat "$tmp/out")"
ok "a second uninstall says that nothing is there"

# The archives for use without Flatpak (tools/archive.sh): unpacked, the app starts in its folder and
# install.sh installs it, both with the rescue system from the archive
echo fake-ramdisk > "$BUILD/rescue/plinius-rescue-aarch64.cpio.lz4"
echo fake-tools > "$BUILD/rescue/plinius-rescue-tools-aarch64.tar.gz"
packed=$("$root/tools/archive.sh" 2>"$tmp/out") || fail "tools/archive.sh: $(cat "$tmp/out")"
mapfile -t archives <<< "$packed"
version=$(cd "$root" && git show HEAD:plinius-boot | sed -n 's/^VERSION = "\(.*\)"$/\1/p')
[[ ${archives[*]} == "$BUILD/release/plinius-boot.tar.gz $BUILD/release/plinius-boot.zip" ]] ||
	fail "unexpected archives: ${archives[*]}"

# Started from an unpacked folder: the app finds the ramdisk and tools in it, and its translations
finds_data() {
	(cd "$tmp" && LANGUAGE=de LC_ALL=C.UTF-8 python3 -I -B -c '
import importlib.machinery, importlib.util, os, sys
loader = importlib.machinery.SourceFileLoader("plinius_boot", sys.argv[1])
app = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(app)
assert os.path.realpath(app.rescue_ramdisk()) == os.path.realpath(sys.argv[2]), app.rescue_ramdisk()
assert os.path.realpath(app.rescue_tools()) == os.path.realpath(sys.argv[3]), app.rescue_tools()
assert app._("About {name}") == "Über {name}", "no translation"
' "$@")
}
for archive in "${archives[@]}"; do
	unpacked=$tmp/unpacked-${archive##*.}
	mkdir "$unpacked"
	case $archive in
		*.zip) unzip -q "$archive" -d "$unpacked" ;;
		*) tar -xzf "$archive" -C "$unpacked" ;;
	esac
	dir=$unpacked/plinius-boot-$version
	for f in plinius-boot install.sh uninstall.sh; do
		[[ -x $dir/$f ]] || fail "$f is not executable in ${archive##*/}"
	done
	cmp -s "$dir/plinius-boot" <(cd "$root" && git show HEAD:plinius-boot) || fail "${archive##*/} holds another app than HEAD"
	finds_data "$dir/plinius-boot" "$dir/build/rescue/plinius-rescue-aarch64.cpio.lz4" \
		"$dir/build/rescue/plinius-rescue-tools-aarch64.tar.gz" || fail "the app in ${archive##*/} misses its data"
done
diff <(cd "$tmp/unpacked-gz" && find . | LC_ALL=C sort) <(cd "$tmp/unpacked-zip" && find . | LC_ALL=C sort) ||
	fail "tar.gz and zip differ"
ok "both archives hold the same, executable, and the app runs from the unpacked folder"

# install.sh from the unpacked folder takes the rescue system along, then the folder can go
dir=$tmp/unpacked-gz/plinius-boot-$version
other=$tmp/from-archive
env -u BUILD PREFIX="$other" "$dir/install.sh" > "$tmp/out" || fail "install.sh from the archive failed"
grep -q -- "--build-rescue" "$tmp/out" && fail "the archive's install.sh misses the rescue system"
rm -rf "$tmp/unpacked-gz" "$tmp/unpacked-zip"
finds_data "$other/bin/plinius-boot" "$other/share/plinius-boot/plinius-rescue-aarch64.cpio.lz4" \
	"$other/share/plinius-boot/plinius-rescue-tools-aarch64.tar.gz" || fail "installed from the archive, it misses its data"
PREFIX="$other" "$other/share/plinius-boot/uninstall.sh" > /dev/null || fail "uninstall after the archive failed"
ok "install.sh from the archive installs the rescue system too"

echo "All install and uninstall checks passed"
