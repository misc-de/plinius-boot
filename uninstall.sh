#!/bin/bash
# Removes an installation made with install.sh: exactly the files listed in installed-files.
# Works from the repository and from share/plinius-boot in the prefix, where install.sh puts a copy.
#
#   ./uninstall.sh
#   PREFIX=/some/dir ./uninstall.sh
#
# Backups, the app's keys and the original images always stay, there is deliberately no option to
# delete them: a phone may not start again without its original, and a rescue system on a phone
# only lets in the key that made it. The app's cache goes, it only holds temporary copies.
set -euo pipefail

own=share/plinius-boot

die() { echo "Error: $*" >&2; exit 1; }

case ${1:-} in
	"") ;;
	-h|--help) sed -n '2,10p' "$0" | sed 's/^# \?//'; exit 0 ;;
	*) die "unknown option $1, see --help" ;;
esac

[[ $(id -u) -ne 0 ]] || die "not as root, the installation belongs to your own user"

# The copy next to the list knows its prefix, the one in the repository asks the environment
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ -z ${PREFIX:-} ]]; then
	if [[ -f $here/installed-files ]]; then
		PREFIX=$(cd "$here/../.." && pwd)
	else
		PREFIX=$HOME/.local
	fi
fi
list=$PREFIX/$own/installed-files
[[ -f $list ]] || die "no installation found in $PREFIX (a package or Flatpak is removed with its own tool)"

# Files must not vanish under a running app: it may be writing to a phone right now
if pgrep -u "$(id -u)" -f '^[^ ]*python[0-9.]* ([^ ]*/)?plinius-boot( |$)' >/dev/null; then
	die "Plinius Boot is running, close it first (it may be writing to a phone)"
fi

# Read before anything is removed, the list is among the files
mapfile -t files < "$list"
for f in "${files[@]}"; do
	# Only plain relative paths: a damaged list must not reach outside the prefix
	case $f in /*|*..*|"") echo "Skipping $f" >&2; continue ;; esac
	rm -f -- "${PREFIX:?}/$f"
	# Only folders of the app itself, never shared ones such as bin or share/applications
	case $f in
		share/locale/*/LC_MESSAGES/*)
			d=$PREFIX/${f%/*}
			rmdir --ignore-fail-on-non-empty "$d" "${d%/*}" 2>/dev/null || true
			;;
	esac
done
rmdir --ignore-fail-on-non-empty "$PREFIX/$own" "$PREFIX/share/licenses/plinius-boot" 2>/dev/null || true
update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" 2>/dev/null || true

config=${XDG_CONFIG_HOME:-$HOME/.config}/plinius-boot
data=${XDG_DATA_HOME:-$HOME/.local/share}/plinius-boot
cache=${XDG_CACHE_HOME:-$HOME/.cache}
rm -rf -- "$cache/plinius-boot" "$cache/plinius-backup"

echo "Plinius Boot is removed from $PREFIX."
echo "Kept on purpose, delete them by hand only if you are sure:"
echo "  your backups, in the folder set in the app"
[[ ! -d $config ]] || echo "  $config   the keys; rescue systems on your phones only let this key in"
[[ ! -d $data ]] || echo "  $data   the original images; a phone may not start again without them"
