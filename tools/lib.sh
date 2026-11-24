# Shared paths and helper functions for the rescue system's scripts (sourced, not executed).
# Everything runs without root: user namespace (unshare --map-auto) + qemu-binfmt.
# BUILD and MIRRORS can be overridden from the environment.

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD=${BUILD:-$ROOT/build}
DOWNLOADS=$BUILD/downloads
CHROOT=$BUILD/chroot/root
PKGCACHE=$BUILD/cache/pacman
KEYRING=archlinuxarm

# Several mirrors, because individual ones hang now and then; pacman then tries the next one.
# de3 is the only one with a valid certificate: first, over https. The databases of Arch Linux
# ARM are unsigned, over http they could be swapped for old ones (updates held back); the others
# are only a fallback. Packages are signed either way.
MIRRORS=${MIRRORS:-"https://de3.mirror.archlinuxarm.org http://de.mirror.archlinuxarm.org http://eu.mirror.archlinuxarm.org http://mirror.archlinuxarm.org"}
MIRROR=${MIRRORS%% *}

# Prints the pacman mirrorlist for $MIRRORS
mirrorlist() {
	local m
	for m in $MIRRORS; do echo "Server = $m/\$arch/\$repo"; done
}

# die: print an error and exit; msg: progress message (both to stderr)
die() { echo "Error: $*" >&2; exit 1; }
msg() { echo "==> $*" >&2; }

# Checks that the host can run the build: user namespaces with subordinate IDs and a qemu binfmt
# handler usable inside a chroot.
check_host() {
	unshare --map-auto --map-root-user true 2>/dev/null ||
		die "user namespaces with --map-auto not possible (check /etc/subuid)"
	local f=/proc/sys/fs/binfmt_misc/qemu-aarch64
	[[ -r $f ]] || die "qemu-aarch64 binfmt missing (package qemu-user-static-binfmt)"
	# F = interpreter is opened at registration time, otherwise qemu does not work in the chroot
	grep -q '^flags: .*F' "$f" || die "qemu-aarch64 binfmt without F flag"
}

# ns_run SCRIPT [ARGS...]
# Runs a bash script as namespace root (own mount and PID namespace; everything mounted
# disappears with it). ARGS become $1... of the script. The script can use the helpers
# chroot_mounts and in_chroot defined below.
ns_run() {
	local script=$1; shift
	mkdir -p "$PKGCACHE"
	PKGCACHE=$PKGCACHE unshare --map-auto --map-root-user --mount --pid --fork --kill-child -- \
		/bin/bash -euo pipefail -c "
			# Prepares \$r as a chroot: dev/proc/sys/tmp, shared pacman cache, host resolv.conf
			chroot_mounts() {
				local r=\$1
				mount --bind \"\$r\" \"\$r\"   # pacman needs / to be a mount point (CheckSpace)
				mount --rbind /dev \"\$r/dev\"
				mount -t proc proc \"\$r/proc\"
				mount --rbind /sys \"\$r/sys\"
				mount -t tmpfs tmpfs \"\$r/tmp\"
				mkdir -p \"\$r/var/cache/pacman/pkg\"
				mount --bind \"\$PKGCACHE\" \"\$r/var/cache/pacman/pkg\"
				rm -f \"\$r/etc/resolv.conf\"
				cp -L /etc/resolv.conf \"\$r/etc/resolv.conf\"
			}
			# Runs a command in chroot \$1 with a clean environment
			in_chroot() {
				local r=\$1; shift
				chroot \"\$r\" /usr/bin/env -i HOME=/root LANG=C.UTF-8 TERM=dumb \
					PATH=/usr/local/sbin:/usr/local/bin:/usr/bin \"\$@\"
			}
			$script" ns_run "$@"
}

# Deletes directories containing files owned by mapped UIDs (not removable as the host user).
ns_rm() {
	unshare --map-auto --map-root-user rm -rf -- "$@"
}
