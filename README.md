# Plinius Boot

A GTK4/libadwaita app for the PC that installs Plinius on a Volla Phone Plinius in one go: onto the
memory card next to Volla OS, or instead of Volla OS. Under **Advanced** it detects a phone connected
over USB-C, backs up its partitions and flashes them, unlocks its bootloader and starts a rescue system on it. It supports the Volla Phone Plinius (Plus) and the FuriLabs FLX1/FLX1s; other
phones with GPT partition names are handled as "unknown phone".

fastboot cannot read partitions, so the app can boot a small **rescue system** on the phone. It
reaches the phone over the USB network and mounts nothing, which makes a backup consistent and lets
`super`, `userdata` and the device data be restored as well.

> Writing partitions can leave a phone unable to start. Device data such as the IMEI and MAC
> addresses is in the backups: keep them private, and restore them only to the same phone. The app
> checks this, but the risk stays yours.

## Installing Plinius

The app goes in four steps, shown as a bar at the top: **Connect, Check, Download, Install**. One button
per step; what is not needed right away is folded away. The download runs while the phone is checked: the
bar shows its progress under its step, and ticks it when it is done before the check.

1. **Connect:** the start page asks for the cable; how to turn on the developer options, "OEM unlocking"
   and "USB debugging" is folded under it. It names the newest image from
   `https://plinius.cais.de/images/latest.json` (name, https link on the same server, size, SHA-256), but
   downloads nothing yet. A phone that turns up opens step 2 by itself.
2. **Check:** what is known about the phone so far, then one click. That click decides: only now the image
   starts downloading (step 3). The app goes on by itself while the phone restarts: into the bootloader, unlocking it if it is still locked (this erases the phone's data and
   waits for a click and Volume up on the phone), into the rescue system, which reads the memory card. If
   "OEM unlocking" is still off, the app restarts the phone into Volla OS, shows the two taps there, and
   after "Continue" takes it back to the bootloader.
3. **Download:** into `~/.local/share/plinius-boot/images`, over a metered connection only on a click; an
   image downloaded before is only checked. **Pause** keeps what came so far as `.part`, and **Resume** goes
   on from there, as does a download that stopped by itself or with the app; a damaged one is deleted.
   Pausing and resuming also work during step 2, below what the page shows there. The folder keeps two images, the new one and the one before it; older ones and unfinished
   downloads of them leave. If nothing is published, an image file is chosen instead. The step shows only if
   the phone is ready before the image, and goes on by itself once the image is checked.
4. **Install:** first the question where Plinius should go, with nothing chosen: **on the memory card**
   (recommended, Volla OS stays) or **inside the phone** (instead of Volla OS). The button waits for the
   answer. Below it the image and the language, with **Change** for another image file or language. Inside
   the phone, the page first asks for a backup of Volla OS on this computer, as the way back: one status, one
   button, and a page with only the storage location and the size. Which partitions that is (the boot
   partitions of the slot, `userdata` unless unlocking just erased it, `init_boot` with its original) is said
   on the Advanced pages only. Where it
   goes on the card is chosen by the app, the safest way first, and said without partition numbers:
   - **A card with a boot menu** (set up by the Volla Boot Manager or by this app): unused partitions, or
     free space for a new place, both shown as "into the free space", where nothing is lost. If neither is
     there, the ways there are show below the card: instead of one of its systems (naming the personal files
     of other systems that are lost with it), or the card set up anew, both with one more confirmation.
   - **A card without a boot menu**, as it comes from the shop: set up the way the Volla Boot Manager does it
     (`Android-Boot-Manager/App`, `DroidBootFlow.kt`), so all of it is erased: a new GPT, `abm_settings`
     (type 8301, a 41st of the card, 512 MiB to 4 GiB) with `db/db.conf` (`timeout 5`) and
     `db/entries/real.conf` for Volla OS, a system partition of up to 40 GiB and the rest for data.

   Images that ask for the PIN at their first start need nothing more. For older ones the page asks for a
   **PIN** for the lock screen, 4 to 12 digits. Only its SHA-512 crypt hash goes to the phone, into
   `/etc/shadow` of the system that was just written, for the user `plinius`. Images without `-P` have that
   password locked, and nobody would get past the lock screen.

On the card Plinius starts with its own kernel and ramdisk from `/usr/share/plinius-boot` in the image, copied
to `abm_settings/plinius/`, never with the kernel of another system. Its entry is written last, then
`db/last_index`, so that the menu starts Plinius by itself. The bootloader counts the entries the way lwext4
goes through the directory as stored, and Linux lists a directory with a hash index in another order, so the
app reads the directory from the partition itself (`entry_order`). Then the original `init_boot` goes back and
the phone starts. Instead of Volla OS it is the way described under 7. below.

Not tried on a real phone yet: setting up a card from the shop, which only the bootloader of a Plinius can
confirm; installing into free space; the whole way through in one go.

## Advanced: systems, boot menu and backups

**Advanced…** in the menu of the installation page leads to the phone's page from before. It leads through
the work step by step, in all its languages:

1. **Tools:** at every start it runs adb, fastboot, ssh, ssh-keygen and ip. What is missing or broken is
   listed with the command that provides it on Arch, Debian and Fedora and their relatives.
2. **Phone:** the start page lists the connected phone and the phones known from earlier backups. A phone that
   is connected opens its page by itself. It has three tabs, **Systems**, **Boot menu** and **Backups**.
   Systems checks that this is a Plinius with an unlocked bootloader, and if not, it says how to unlock it;
   below are the ways to change systems and to the rescue system.
3. **Boot menu:** this tab lists the entries of the phone's boot menu in their order. Each one leads to the
   system it starts, its partitions with their use, its boot entry as the bootloader reads it, and how the
   memory card is divided, drawn as a bar with the entry's partitions standing out.
4. **Changing systems:** a page of its own leads into the bootloader and starts the rescue system, then
   shows the systems. It is a separate step, because only the rescue system can see all of it.
5. **On the memory card:** install a system from an image file on the place of another one, move a system to
   another place, or let two systems swap places. Several systems need a memory card, because the bootloader
   reads its boot menu from there. Volla OS stays on its two slots.
6. **Making room:** a partition that uses little of its space can become smaller, and the room can hold a new
   place. As an experiment, places can also be made in the phone's own storage. That takes room from `userdata`
   and erases all data of Volla OS. The partition table is saved first and can be put back from the app.
7. **On the phone itself:** a system from an image file can replace Volla OS. It then starts without a memory
   card, from much faster storage. All data of Volla OS is erased; the way back is a backup or the Volla OS package.
   The bootloader and the partition table stay as they are. So far this has only run in a virtual machine.

Before anything is overwritten, the app says what is lost, and that includes what it can tell about other
systems: for example a system that keeps its `/home` on the data of another one, because systemd mounts the
first partition of the type "Linux /home" by itself. It keeps a copy of what it overwrites on the PC if you
want, checks everything it writes, and changes the boot entry last, so that a stop in between leaves the menu
as it was. A swap that stops goes on where it stopped.

**Backups** shows only the backups of this phone, newest first, and the storage location. "Create backup…"
leads to the list of all partitions, where you choose what to save. Starting the rescue system, unlocking the
bootloader and writing an original back are on Systems, under "Rescue system and bootloader".

## Access paths

All of them work over USB only. The app shows the one that can read if a phone appears several times.

| State of the phone | Detected through | Back up | Restore |
|---|---|---|---|
| Linux (Ubuntu Touch, FuriOS, Droidian, Arch) | USB network `10.15.19.82`, SSH | yes (sudo) | yes, partitions not in use |
| Android / Volla OS | adb | with root only | with root only |
| Bootloader / fastbootd | fastboot | no | with an unlocked bootloader; can start the rescue system |
| Rescue system | USB network, SSH with the app's own key | everything | everything except preloader and GPT |
| MediaTek download mode | USB IDs | no | no |

A backup is a folder with `manifest.json` and one `<name>.img.zst` per partition. The SHA-256 checksum
covers the raw data and is compared before and after restoring.

## Renamed from `plinius-backup`

Up to version 0.7 the project was called `plinius-backup`. On its first start the app moves
`~/.config/plinius-backup` and `~/.local/share/plinius-backup` to the new name, and existing backups
keep working.

## Installing

All ways install the same app, and they share your keys, original images and backups. Use one of them.
Translations: de, es, fr, it, nl, pl, pt, ru.

### Flatpak

Brings everything along, including adb, fastboot and the rescue system. It is available for x86_64 only
so far. In any folder:

```sh
curl -LO https://github.com/misc-de/plinius-boot/releases/latest/download/org.plinius.Boot-x86_64.flatpak
flatpak install --user org.plinius.Boot-x86_64.flatpak
flatpak run org.plinius.Boot
```

If Flatpak asks whether to add Flathub and install the GNOME 51 runtime, answer yes. The runtime is about
430 MB to download and 1.1 GB installed, unless you already have it. The downloaded file can be deleted
afterwards.

To update, run the first two commands again. To remove the app, run
`flatpak uninstall org.plinius.Boot`, and `flatpak uninstall --unused` for a runtime nothing else needs.

The sandbox is wide open by necessity: the app gets all devices (USB, for adb and fastboot), the
network (the phone's USB network, update packages) and your home folder (backups, keys, originals).
On the host, access to phones still depends on udev rules (Arch/Manjaro: package `android-udev`).

### Without Flatpak, from the archive

Every release also has `plinius-boot.tar.gz` and `plinius-boot.zip`, the same content in both: the app
with its rescue system, about 12 MB. Unpack one and start the app right in its folder, nothing is
installed then; or install it for your user, into `~/.local` with a menu entry:

```sh
curl -LO https://github.com/misc-de/plinius-boot/releases/latest/download/plinius-boot.tar.gz
tar -xzf plinius-boot.tar.gz
cd plinius-boot-*/
./plinius-boot     # starts it from here
./install.sh       # or installs it for your user, without root; the folder can go afterwards
```

The programs come from your system, `./install.sh` names whatever is missing, with its package:

| | Arch, Manjaro | Debian 13, Ubuntu 25.04 or later | Fedora 41 or later |
|---|---|---|---|
| Python 3.13 or later with GTK 4 and libadwaita 1.6 or later | `python-gobject gtk4 libadwaita` | `python3-gi gir1.2-adw-1` | `python3-gobject libadwaita` |
| zstd for Python, needed before Python 3.14 | (in Python 3.14) | `python3-zstandard` | `python3-zstandard` |
| ssh, adb, fastboot, ip, msgfmt | `openssh android-tools iproute2 gettext` | `openssh-client adb fastboot iproute2 gettext` | `openssh-clients android-tools iproute gettext` |
| USB access to phones (udev rules) | `android-udev` | `android-sdk-platform-tools-common` | see below |

Whether the phone is allowed through USB shows `adb devices` while it is connected: it must list the
phone without "no permissions". Ubuntu 24.04 has libadwaita 1.5, which is too old; the Flatpak works there. Instead of
python-zstandard the app also takes `backports.zstd`, which is the same as zstd in Python 3.14.

To update, unpack the new archive and run `./install.sh` again. `./uninstall.sh`, from the folder or
from `~/.local/share/plinius-boot`, removes the installation.

### For your user, from the repository

The same `install.sh` works in a clone of the repository, which has no rescue system in it:

```sh
./install.sh                  # into ~/.local; run it again to update
./install.sh --build-rescue   # builds the rescue system first
./uninstall.sh                # also works from share/plinius-boot in the prefix
```

`PREFIX=/some/dir` chooses another place. The repository can be deleted after installing.

### Arch package, or from the source tree

```sh
makepkg -si        # system-wide, needs the rescue ramdisk built first
./plinius-boot     # without installing, compiles the translations in po/ by itself
```

### What removing leaves behind

Your backups, `~/.config/plinius-boot` (the keys) and `~/.local/share/plinius-boot` (the original
images) always stay, and no uninstaller has an option to delete them. A phone may not start again
without its original, and a rescue system on a phone only lets in the key that made it. Delete them
by hand if you are sure. `uninstall.sh` refuses to run while the app is open.

## Building the rescue system

The ramdisk is not in the repository, the Flatpak and the archives contain it. Without it, everything but the rescue system works. Building
needs no root, only user namespaces with subordinate IDs (`/etc/subuid`) and
`qemu-user-static-binfmt`, plus `curl`, `bsdtar`, `gpg` and `lz4`.

```sh
tools/chroot-init.sh    # aarch64 build chroot in build/, verified against the Arch Linux ARM key
rescue/mkrescue.sh      # build/rescue/plinius-rescue-aarch64.cpio.lz4, where the app looks for it
rescue/test-rescue.sh   # boots it in QEMU and tests it, needs qemu-system-aarch64, sfdisk and cryptsetup
```

`BUILD=/some/dir` puts the chroot and all build output elsewhere.

`mkrescue.sh` also builds `build/rescue/plinius-rescue-tools-aarch64.tar.gz` with `cryptsetup` and
`dmsetup`, for opening and checking an encrypted data partition in the rescue system. They do not fit
into its ramdisk: the bootloader of the Plinius loads at most 22,020,096 bytes of ramdisk, vendor
ramdisk included. The app unpacks the archive into the rescue system whenever it logs in there; the
rescue system runs from RAM, so it is gone after the next start. Without the app:

```sh
ssh -i ~/.config/plinius-boot/rescue_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
	root@10.15.19.82 'tar -xzf - -C /' < build/rescue/plinius-rescue-tools-aarch64.tar.gz
```

Then, for example, `cryptsetup open /dev/disk/by-partlabel/userdata data` (in a terminal of its own with
`ssh -t`, for the passphrase), `e2fsck -f /dev/mapper/data` and `cryptsetup close data`.

## Building the Flatpak

Every version gets a Flatpak. It needs `flatpak-builder`, the rescue ramdisk from above, and for its
first build some time, since protobuf and android-tools are built from source. aarch64 is built under
qemu-user (`qemu-user-static-binfmt`) and is much slower.

```sh
tools/flatpak.sh                 # build/flatpak/org.plinius.Boot-<arch>.flatpak, x86_64 and aarch64
tools/flatpak.sh --arch x86_64   # only this architecture
tools/flatpak.sh --arch x86_64 --release   # also publishes it as the release of the current version
tools/archive.sh                 # build/release/plinius-boot.tar.gz and .zip, for use without Flatpak
```

A release also carries the two archives of `tools/archive.sh`: the committed app with the rescue ramdisk
and its tools in `build/rescue/`, where the app and `install.sh` look for them.

The aarch64 build has never been finished, so releases contain x86_64 only for now.

## Testing

```sh
tests/test-install.sh   # installer and uninstaller, in throw-away folders
tests/test-guide.py     # the logic of the guided flow and of the installation, without a phone
rescue/test-rescue.sh   # the rescue system, in QEMU
rescue/test-systems.sh  # installing, moving and swapping, setting up a card and the PIN, in QEMU with an emulated SD card
```

`tests/test-install.sh` also builds both archives and runs the app from them. The zstd fallback for Python
before 3.14 is tested by `tests/test-guide.py` when python-zstandard is installed; run it with Python 3.13 to
test the whole app on top of it.

## Repository layout

| Path | Contents |
|---|---|
| `plinius-boot` | the app, a single Python file |
| `org.plinius.Boot.desktop`, `org.plinius.Boot.svg` | the desktop entry and the icon |
| `po/` | translations |
| `rescue/` | the rescue system: `mkrescue.sh`, `/init` (`rescue-init`), `plinius-splash.c`, `plinius-reboot.c`, and the QEMU test |
| `tools/` | build chroot, shared script helpers, the Flatpak build, the archives |
| `install.sh`, `uninstall.sh` | per-user installation |
| `org.plinius.Boot.yaml`, `org.plinius.Boot.metainfo.xml` | Flatpak manifest and app metadata |
| `PKGBUILD` | Arch package of this working tree |
| `tests/` | tests of the installer and the guided flow |

## License

The app and everything in this repository: MIT, see [LICENSE](LICENSE).

The Flatpak also contains android-tools (Apache-2.0 and others; adb, fastboot and their helpers) and
`ip` from iproute2 (GPL-2.0-or-later), and it runs on the GNOME runtime.

The rescue ramdisk, once built, also contains these Arch Linux ARM packages under their own licenses:

| Package | License |
|---|---|
| busybox | GPL-2.0-only |
| dropbear | MIT |
| e2fsprogs | GPL, LGPL, MIT |
| gptfdisk | GPL-2.0 |
| terminus-font (`ter-v16b`) | OFL |
| glibc | GPL-2.0-or-later, LGPL-2.1-or-later |
| libxcrypt | LGPL |
| zlib | Zlib |
| util-linux-libs (libblkid, libuuid) | several, see the package |
| popt | MIT-style (custom) |
| gcc-libs (libgcc_s, libstdc++) | GPL-3.0-or-later WITH GCC-exception-3.1 |

The tools archive (`plinius-rescue-tools-aarch64.tar.gz`) contains glibc, zlib, util-linux-libs and popt as
above, and:

| Package | License |
|---|---|
| cryptsetup | GPL-2.0-or-later, LGPL-2.1-or-later |
| device-mapper (dmsetup, libdevmapper) | GPL-2.0-only, LGPL-2.1-only |
| openssl (libcrypto) | Apache-2.0 |
| json-c | MIT |
| brotli | MIT |
| zstd | BSD-3-Clause or GPL-2.0-only |
