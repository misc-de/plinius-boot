#!/usr/bin/env python3
"""Tests installing, moving and swapping against a real rescue system: called by test-systems.sh, which
boots it in QEMU with an emulated SD card. Runs the app's own functions and scripts over ssh; only the
way to log in differs. Every partition is checked by its contents afterwards."""
import hashlib
import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types

sys.dont_write_bytecode = True
app_path, key, port, work = sys.argv[1:5]
tmp = tempfile.mkdtemp(dir=work)
for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[variable] = tmp
os.environ["XDG_RUNTIME_DIR"] = tmp
os.environ["LC_ALL"], os.environ["LANGUAGE"] = "C.UTF-8", "en"
loader = importlib.machinery.SourceFileLoader("plinius_boot", app_path)
app = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(app)
# Nothing here may reach real data
assert app.CONFIG_HOME == tmp and app.DATA_HOME == tmp and app.CACHE.startswith(tmp), "not in the throw-away folder"
base = os.path.join(tmp, "backups")


def ok(text):
    print(f"  ok: {text}", file=sys.stderr, flush=True)


def fail(text):
    sys.exit(f"FAILED: {text}")


class VmAccess(app.SshAccess):
    """As for a phone on the USB network, with the test's own key and port."""

    def __init__(self):
        super().__init__("127.0.0.1", "root", int(port), usbnet=False)
        self.root = ""

    def command(self, remote):
        return ["ssh", "-p", port, "-i", key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "--", "root@127.0.0.1", remote]


access = VmAccess()
device = types.SimpleNamespace(rescue=True, access=access, serial=access.info().get("serial"), mode="ssh")
if not device.serial:
    fail("the rescue system has no serial number")


class SilentPage:
    def progress(self, *_a):
        pass

    def add_line(self, *_a):
        pass


def job():
    """The real job; idle_add has no main loop here, so its calls just wait in the queue."""
    return app.Job(SilentPage())


def sh(script, timeout=120):
    rc, out, err = access.run_root(script, timeout)
    if rc != 0:
        fail(f"{script!r}: {err or out}")
    return out


def fingerprint(part):
    """The contents of a file system: a marker file, read in a mount namespace of nothing."""
    return sh(f"""m=$(mktemp -d); mount -t ext4 -o ro /dev/{part} "$m" && cat "$m/marker" 2>/dev/null; umount "$m"; rmdir "$m"
""").strip()


def boot_entry(name):
    return sh(f"""m=$(mktemp -d); mount -t ext4 -o ro /dev/{CARD}p1 "$m"; cat "$m/db/entries/{name}" 2>/dev/null; umount "$m"; rmdir "$m"
""")


def entry_mode(name):
    return sh(f"""m=$(mktemp -d); mount -t ext4 -o ro /dev/{CARD}p1 "$m"; stat -c '%u %a' "$m/db/entries/{name}"; umount "$m"; rmdir "$m"
""").strip()


def read():
    card = access.card()
    if not card:
        fail("no memory card seen: the script found no SD card")
    return card, {e["file"]: e for e in card["entries"]}


card, entries = read()
CARD = card["disk"]
# Stufe 1: what the app sees
if [p["number"] for p in card["parts"]] != [1, 2, 3, 4, 5, 6, 7]:
    fail(f"partitions: {[p['number'] for p in card['parts']]}")
ok(f"an SD card as on the phone: {CARD} with 7 partitions")
if card["settings"] != f"{CARD}p1":
    fail(f"partition with the boot entries: {card['settings']}")
ok("the partition with the boot entries is found")
ut, arch = entries["rom0.conf"], entries["rom1.conf"]
if (ut["system"]["number"], ut["data"]["number"], arch["system"]["number"], arch["data"]["number"]) != (2, 3, 4, 5):
    fail("places wrong")
if arch["kernel"] != "rom0" or app.uses_kernel_of(card, ut) != ["Arch"]:
    fail(f"kernel: {arch['kernel']} {app.uses_kernel_of(card, ut)}")
ok("the places, and that Arch starts with the kernel from the folder of Ubuntu Touch")
if card["parts"][2]["type"] != app.GPT_HOME or card["parts"][1]["type"] == app.GPT_HOME:
    fail("GPT types not read")
if not arch["system"].get("gpt_auto") or ut["system"].get("gpt_auto"):
    fail(f"gpt-auto not told apart: arch {arch['system'].get('gpt_auto')}, ut {ut['system'].get('gpt_auto')}")
users = app.home_users(card)
if users != {3: ["Arch"]}:
    fail(f"/home users: {users}")
if not app.place_warnings(card, ut) or app.place_warnings(card, arch):
    fail("warnings on the wrong place")
ok("Arch keeps /home on the data of Ubuntu Touch: found by itself, warned on the right place")
if not card["parts"][2]["fs"] or card["parts"][2]["fs"]["bytes"] < 1 << 20:
    fail("file system sizes not read")
free = app.free_pairs(card)
if len(free) != 1 or (free[0]["system"]["number"], free[0]["data"]["number"]) != (6, 7):
    fail(f"free places: {free}")
ok("the file systems and the one free place")


def contents():
    """Which system is where now."""
    return {n: fingerprint(f"{CARD}p{n}") for n in (2, 3, 4, 5)}


START = {2: "ut-system", 3: "ut-data", 4: "arch-system", 5: "arch-data"}
if contents() != START:
    fail(f"start state: {contents()}")

# Refusals
image_dir = os.path.join(tmp, "images")
os.makedirs(image_dir)
for name, tree in (("good", "good"), ("no-android", "noandroid"), ("no-release", "norelease")):
    root = os.path.join(image_dir, tree)
    os.makedirs(os.path.join(root, "etc"))
    os.makedirs(os.path.join(root, "usr/lib"))
    if tree != "noandroid":
        os.makedirs(os.path.join(root, "var/lib/lxc/android"))
        with open(os.path.join(root, "var/lib/lxc/android/android-rootfs.img"), "wb") as f:
            f.write(b"x")
    if tree != "norelease":
        with open(os.path.join(root, "usr/lib/os-release"), "w") as f:
            f.write('PRETTY_NAME="New System 1.0"\n')
        os.symlink("../usr/lib/os-release", os.path.join(root, "etc/os-release"))
    with open(os.path.join(root, "marker"), "w") as f:
        f.write("new-system\n")
    subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "4096", "-d", root, os.path.join(image_dir, name + ".img"), "16M"], check=True)
good = os.path.join(image_dir, "good.img")
packed = good + ".zst"
subprocess.run(["zstd", "-q", "-f", good, "-o", packed], check=True)
big = os.path.join(image_dir, "big.img")
big_root = os.path.join(image_dir, "good")
subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "4096", "-d", big_root, big, "200M"], check=True)
small_blocks = os.path.join(image_dir, "small-blocks.img")
subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "1024", "-d", big_root, small_blocks, "16M"], check=True)

image = app.SystemImage(good)
image.check()
if image.problem or image.name != "New System 1.0" or image.size != 16 << 20:
    fail(f"good image: {image.problem!r} {image.name!r} {image.size}")
ok("an image is read without mounting it: name, size, and a relative link")
for name, expected in (("no-android.img", "android-rootfs"), ("no-release.img", "os-release")):
    checked = app.SystemImage(os.path.join(image_dir, name))
    checked.check()
    if expected not in checked.problem:
        fail(f"{name}: {checked.problem!r}")
garbage = os.path.join(image_dir, "garbage.img")
with open(garbage, "wb") as f:
    f.write(os.urandom(1 << 20))
checked = app.SystemImage(garbage)
checked.check()
if "ext4" not in checked.problem:
    fail(f"garbage: {checked.problem!r}")
ok("images that could not start, or are none at all, are refused")
unpacked = app.SystemImage(packed)
unpacked.check()
if unpacked.problem or unpacked.size != 16 << 20 or not unpacked.temporary:
    fail(f"packed image: {unpacked.problem!r}")
ok("a compressed image too")

too_big = app.SystemImage(big)
too_big.check()
try:
    app.run_install(job(), device, card, ut, too_big, False, "x", False, base)
    fail("an image larger than the partition was written")
except app.Failed as err:
    if "Nothing was changed" not in str(err):
        fail(str(err))
if contents() != START:
    fail("a refused image changed something")
ok("an image larger than its place is refused, and nothing changes")

other = types.SimpleNamespace(**vars(device))
other.serial = "ANOTHER"
for call in (lambda: app.run_install(job(), other, card, ut, image, False, "x", False, base),
             lambda: app.run_move(job(), other, card, ut, free[0], False, base),
             lambda: app.begin_swap(other, card, ut, arch, base)):
    try:
        call()
        fail("another phone was accepted")
    except app.Failed as err:
        if "different phone" not in str(err):
            fail(str(err))
ok("another phone on the cable is refused")

changed = app.parse_card(app.CARD_SCRIPT and access.run_root(app.CARD_SCRIPT, 120)[1])
changed["entries"][1]["raw"] = changed["entries"][1]["raw"] + ["title changed"]
try:
    app.run_install(job(), device, changed, ut, image, False, "x", False, base)
    fail("a stale plan was accepted")
except (app.Failed, StopIteration) as err:
    if isinstance(err, app.Failed) and "changed" not in str(err):
        fail(str(err))
if contents() != START:
    fail("a stale plan changed something")
ok("a plan that no longer matches the card is refused")

# Stufe 2: install on the place of Ubuntu Touch; its data stays, because Arch keeps /home there
before_entry = boot_entry("rom0.conf")
card, entries = read()
app.run_install(job(), device, card, entries["rom0.conf"], image, False, "New System", True, base)
now = contents()
if now[2] != "new-system" or now[3] != "ut-data" or now[4] != "arch-system" or now[5] != "arch-data":
    fail(f"after installing: {now}")
ok("installed: the system is new, the data and the other system are untouched")
card, entries = read()
fs = entries["rom0.conf"]["system"]["fs"]
if fs["bytes"] < entries["rom0.conf"]["system"]["size"] - (8 << 20):
    fail(f"file system not fitted: {fs['bytes']} of {entries['rom0.conf']['system']['size']}")
ok("the file system uses the whole partition")
entry = boot_entry("rom0.conf")
if "title New System\n" not in entry or "linux rom0/zImage\n" not in entry or "xpart 2:3\n" not in entry:
    fail(f"entry: {entry}")
if entry_mode("rom0.conf") != "1000 600":
    fail(f"owner and mode: {entry_mode('rom0.conf')}")
if ".new" in sh(f"""m=$(mktemp -d); mount -t ext4 -o ro /dev/{CARD}p1 "$m"; ls "$m/db/entries"; umount "$m"; rmdir "$m"
"""):
    fail("a temporary entry stayed")
ok("the entry has its new title and keeps everything else, owner and mode as the manager makes them")
states = app.load_states(base, device.serial)
if len(states) != 1 or states[0]["kind"] != "copy" or not states[0]["done"] or set(states[0]["partitions"]) != {"2"}:
    fail(f"copy: {states}")
if app.list_backups(base):
    fail("a copy appears among the backups")
ok("the old system is kept on the PC, and not offered as a backup")

# ...and back from that copy
app.run_put_back(job(), device, card, states[0])
if contents() != START or boot_entry("rom0.conf") != before_entry:
    fail(f"after putting back: {contents()}")
ok("put back: the old system and its entry are as they were")

# Emptying the data
card, entries = read()
app.run_install(job(), device, card, entries["rom0.conf"], image, True, "New System", False, base)
now = contents()
if now[3] != "" or now[2] != "new-system":
    fail(f"after emptying: {now}")
ok("data emptied on request")

# Cancelled mid-write: the entry must still be the old one, and the next run must work
before_entry = boot_entry("rom1.conf")
card, entries = read()
cancelling = job()
real_copy = cancelling.copy


def copy_and_cancel(read_, write, hasher, done, total, text, limit):
    end = real_copy(read_, write, hasher, done, total, text, min(limit, 1 << 20))
    cancelling.cancel()
    cancelling.check()
    return end


cancelling.copy = copy_and_cancel
try:
    app.run_install(cancelling, device, card, entries["rom1.conf"], image, False, "Half", False, base)
    fail("not cancelled")
except app.Cancelled:
    pass
if boot_entry("rom1.conf") != before_entry:
    fail("a cancelled install changed the entry")
ok("cancelled while writing: the entry is untouched")
card, entries = read()
app.run_install(job(), device, card, entries["rom1.conf"], image, False, "Arch", False, base)
if fingerprint(f"{CARD}p4") != "new-system":
    fail("the install after a cancel failed")
ok("the next run works")

# Bring the known state back by hand for the rest
sh(f"""for n in 2:ut-system 3:ut-data 4:arch-system 5:arch-data; do
	p=${{n%%:*}}; mkfs.ext4 -F -q /dev/{CARD}p$p; m=$(mktemp -d); mount -t ext4 /dev/{CARD}p$p "$m"; echo ${{n##*:}} > "$m/marker"
	mkdir -p "$m/etc"; printf 'NAME=%s\\n' "$p" > "$m/etc/os-release"; umount "$m"; rmdir "$m"
done
""", 300)
if contents() != START:
    fail(f"reset: {contents()}")

# Stufe 3: move Arch to the free partitions. p5 is much bigger than p7, and almost empty: it is made small
card, entries = read()
arch = entries["rom1.conf"]
target = app.free_pairs(card)[0]
if not app.needs_shrinking(arch["data"], target["data"]):
    fail("the data partition should need shrinking")
blocks_before = arch["data"]["fs"]["blocks"]
app.run_move(job(), device, card, arch, target, False, base)
if fingerprint(f"{CARD}p6") != "arch-system" or fingerprint(f"{CARD}p7") != "arch-data":
    fail("moved contents wrong")
ok("moved, also a big and almost empty file system into a small partition")
if contents() != START:
    fail(f"the source was changed: {contents()}")
card, entries = read()
if next(p for p in card["parts"] if p["number"] == 5)["fs"]["blocks"] != blocks_before:
    fail("the source did not get its old size back")
ok("the source stays whole and has its old size back")
entry = boot_entry("rom1.conf")
if "xpart 6:7\n" not in entry or f"systempart=/dev/{CARD}p6 " not in entry or not entry.rstrip().endswith("logo rom1/logo.bin") \
        or f"datapart=/dev/{CARD}p7" not in entry or "rom0/zImage" not in entry:
    fail(f"entry after the move: {entry}")
ok("the entry points to the new place and keeps everything else")

# A move that cannot fit, and a move over another system
card, entries = read()
small = entries["rom1.conf"]
filler = """m=$(mktemp -d); mount -t ext4 /dev/%sp5 "$m"; head -c 104857600 /dev/urandom > "$m/filler"; sync; umount "$m"; rmdir "$m"
""" % CARD
sh(filler, 300)
card, entries = read()
full_part = next(p for p in card["parts"] if p["number"] == 5)
if not app.copy_problem(full_part, entries["rom1.conf"]["system"]):
    fail("100 MiB of data should not fit into 32 MiB")
ok("what does not fit is named before anything happens")

# Move back over Ubuntu Touch's place? Not free: its entry leaves the menu and is kept readable
card, entries = read()
app.run_move(job(), device, card, entries["rom1.conf"], entries["rom0.conf"], True, base)
if fingerprint(f"{CARD}p2") != "arch-system" or fingerprint(f"{CARD}p3") != "arch-data":
    fail("moving over a system failed")
files = sh(f"""m=$(mktemp -d); mount -t ext4 -o ro /dev/{CARD}p1 "$m"; ls "$m/db/entries" "$m/db/removed"; umount "$m"; rmdir "$m"
""")
if "rom0.conf" not in files.split("db/removed")[1] or "rom0.conf" in files.split("db/removed")[0]:
    fail(f"the replaced entry: {files}")
ok("moved over another system: its entry leaves the menu and stays readable, a copy on the PC")

# Stufe 4: reset and swap
sh(f"""m=$(mktemp -d); mount -t ext4 /dev/{CARD}p1 "$m"; mv "$m/db/removed/rom0.conf" "$m/db/entries/rom0.conf"; umount "$m"; rmdir "$m"
for n in 2:ut-system 3:ut-data 4:arch-system 5:arch-data; do
	p=${{n%%:*}}; mkfs.ext4 -F -q /dev/{CARD}p$p; m=$(mktemp -d); mount -t ext4 /dev/{CARD}p$p "$m"; echo ${{n##*:}} > "$m/marker"
	mkdir -p "$m/etc"; printf 'NAME=%s\\n' "$p" > "$m/etc/os-release"; umount "$m"; rmdir "$m"
done
""", 300)
card, entries = read()
rom1 = next(e for e in card["entries"] if e["file"] == "rom1.conf")
rom1_raw = app.entry_text(rom1, system={"number": 4, "name": f"{CARD}p4"}, data={"number": 5, "name": f"{CARD}p5"})
app.write_entries(access, card, {"rom1.conf": rom1_raw})
card, entries = read()
if contents() != START or (entries["rom1.conf"]["system"]["number"], entries["rom1.conf"]["data"]["number"]) != (4, 5):
    fail("reset before the swap")

# Interrupted while writing: the second partition fails, then it goes on where it stopped
titles = (entries["rom0.conf"]["title"], entries["rom1.conf"]["title"])
state = app.begin_swap(device, card, entries["rom0.conf"], entries["rom1.conf"], base)
interrupted = job()
real_send = app.send_to_partition
calls = {"n": 0}


def send_then_cut(job_, access_, read_, part, size, done, total, text):
    calls["n"] += 1
    if calls["n"] == 2:
        raise app.Failed("the cable was pulled")
    return real_send(job_, access_, read_, part, size, done, total, text)


app.send_to_partition = send_then_cut
try:
    app.run_swap(interrupted, device, card, state)
    fail("the swap should have stopped")
except app.Failed as err:
    if "cable" not in str(err):
        fail(str(err))
finally:
    app.send_to_partition = real_send
if set(state["partitions"]) != {"2", "3", "4", "5"} or state["written"] != [2]:
    fail(f"state after the cut: {state['partitions'].keys()} {state['written']}")
ok("cut while writing: all four copies were there first, one partition is done")
if fingerprint(f"{CARD}p4") != "ut-system":
    fail("the first partition should be written")
if "xpart 2:3\n" not in boot_entry("rom0.conf"):
    fail("the entries changed before the end")
ok("the entries have not changed yet")

# As after a new start of the app: everything comes from state.json
saved = next(s for s in app.load_states(base, device.serial) if s["kind"] == "swap" and not s["done"])
if saved["written"] != [2]:
    fail(f"loaded state: {saved['written']}")
card, entries = read()
counted = {"n": 0}


def counted_send(job_, access_, read_, part, size, done, total, text):
    counted["n"] += 1
    return real_send(job_, access_, read_, part, size, done, total, text)


app.send_to_partition = counted_send
try:
    app.run_swap(job(), device, card, saved)
finally:
    app.send_to_partition = real_send
if counted["n"] != 3:
    fail(f"went on with {counted['n']} partitions instead of 3")
ok("it goes on where it stopped and does not write the finished partition again")
swapped = {2: "arch-system", 3: "arch-data", 4: "ut-system", 5: "ut-data"}
if contents() != swapped:
    fail(f"after the swap: {contents()}")
ok("the systems and their data have changed places")
a, b = boot_entry("rom0.conf"), boot_entry("rom1.conf")
if "xpart 4:5\n" not in a or f"title {titles[0]}\n" not in a or f"systempart=/dev/{CARD}p4 " not in a:
    fail(f"entry of Ubuntu Touch: {a}")
if "xpart 2:3\n" not in b or f"title {titles[1]}\n" not in b or f"datapart=/dev/{CARD}p3" not in b:
    fail(f"entry of Arch: {b}")
ok("each entry points to the new place of its system")
done = next(s for s in app.load_states(base, device.serial) if s["kind"] == "swap")
if not done["done"]:
    fail("the swap is not marked as finished")
if not all(os.path.exists(os.path.join(done["folder"], p["file"])) for p in done["partitions"].values()):
    fail("the copies are gone")
ok("it is marked as finished, and the copies stay on the PC")
if sh("grep -c plinius-entries /proc/mounts || true").strip() != "0":
    fail("the partition with the entries stayed mounted")
ok("nothing stays mounted")

# Stufe 5: make room. p5 holds 256 MiB, little of it in use
def guid(number, disk=None):
    out = sh(f"sgdisk -i {number} /dev/{disk or CARD}")
    return [line for line in out.splitlines() if "GUID" in line]


card, entries = read()
p5 = next(p for p in card["parts"] if p["number"] == 5)
others = {n: fingerprint(f"{CARD}p{n}") for n in (2, 3, 4, 5, 6, 7)}
before = guid(5)
for too_small in (1 << 20, p5["size"] + (1 << 20)):
    try:
        app.run_shrink(job(), device, card, p5, too_small, False, base)
        fail(f"a size of {too_small} was accepted")
    except app.Failed as err:
        if "Nothing was changed" not in str(err):
            fail(str(err))
ok("sizes that do not work are refused, and nothing changes")
start = p5["start"]
app.run_shrink(job(), device, card, p5, 96 << 20, True, base)
card, entries = read()
p5 = next(p for p in card["parts"] if p["number"] == 5)
if (p5["start"], p5["sectors"]) != (start, (96 << 20) // 512):
    fail(f"after shrinking: {p5['start']} {p5['sectors']}")
if guid(5) != before or p5["type"] != app.GPT_HOME:
    fail("the partition lost its identity")
ok("a partition became smaller: same start, same identity, same type")
if {n: fingerprint(f"{CARD}p{n}") for n in (2, 3, 4, 5, 6, 7)} != others:
    fail("contents changed")
if p5["fs"]["bytes"] < p5["size"] - (2 << 20):
    fail(f"file system not fitted: {p5['fs']['bytes']} of {p5['size']}")
ok("its contents and all others are unchanged, and the file system fits exactly")
if card["largest_free"] < 150 << 20:
    fail(f"the room is not there: {card['largest_free']}")
ok(f"{card['largest_free'] >> 20} MiB are free now")

try:
    app.run_add_place(job(), device, card, 1 << 30, 1 << 30)
    fail("more than there is was accepted")
except app.Failed:
    pass
app.run_add_place(job(), device, card, 32 << 20, 48 << 20)
card, entries = read()
new = {p["number"]: p for p in card["parts"] if p["number"] > 7}
if sorted(new) != [8, 9]:
    fail(f"new partitions: {sorted(new)}")
if new[8]["type"] == app.GPT_HOME or new[9]["type"] == app.GPT_HOME or not new[8]["fs"] or not new[9]["fs"]:
    fail("a new partition has the type that makes systemd mount it")
if new[8]["start"] % app.ALIGN or new[9]["start"] != new[8]["start"] + new[8]["sectors"]:
    fail("new partitions not aligned")
ok("a new place: empty file systems, aligned, and no partition of the type systemd would mount")
if {n: fingerprint(f"{CARD}p{n}") for n in (2, 3, 4, 5, 6, 7)} != others:
    fail("contents changed by adding a place")

# Install on a free place: it gets a new entry
place = next(p for p in app.free_pairs(card) if p["system"]["number"] == 8)
app.run_install(job(), device, card, place, image, True, "Fresh", True, base)
card, entries = read()
if "rom2.conf" not in entries or (entries["rom2.conf"]["system"]["number"], entries["rom2.conf"]["data"]["number"]) != (8, 9):
    fail(f"new entry: {list(entries)}")
entry = boot_entry("rom2.conf")
if "title Fresh\n" not in entry or "rom0/zImage" not in entry or "xupdate" in entry or f"systempart=/dev/{CARD}p8 " not in entry:
    fail(f"new entry: {entry}")
if entry_mode("rom2.conf") != "1000 600" or fingerprint(f"{CARD}p8") != "new-system":
    fail("new entry or system wrong")
ok("installed on a free place: it has its own entry, with the kernel of another system")

# Stufe 6: places in the phone's own storage, on a disk with sectors of 4096 bytes
card, entries = read()
if not card["userdata"] or card["userdata"]["sector_size"] != 4096 or card["userdata"]["disk"] != "vda":
    fail(f"userdata: {card['userdata']}")
ok("userdata is found, on a disk with sectors of 4096 bytes")
config = {"backup_dir": base}
real_save = app.save_config
app.save_config = lambda _config: None
# For backups, as the app knows a phone
device.info = access.info()
device.title = lambda: "Plinius"
device.load_parts = lambda reload=False: access.partitions()
if not device.info.get("boot_id"):
    fail("the rescue system does not tell its start apart")
app.USERDATA_MIN = 64 << 20
device.profile = next(p for p in app.PROFILES if p.id == "volla-plinius-plus")
problem = app.internal_problem(device, card, config, 1, 16 << 20, 16 << 20)
if "original" not in problem:
    fail(f"without the original: {problem!r}")
config["rescue_in"] = {device.serial: {"partition": "init_boot_b", "expected": {}}}
if "less than" not in app.internal_problem(device, card, config, 3, 1 << 30, 1 << 30):
    fail("too much was accepted")
ok("it needs the way out of the rescue system first, and leaves Volla OS its minimum")


def internal_hashes():
    return sh("for n in nvdata mrdump super; do head -c 1048576 /dev/disk/by-partlabel/$n | sha256sum; done")


hashes = internal_hashes()
userdata = card["userdata"]
before = guid(userdata["number"], "vda")
try:
    app.run_internal_places(job(), device, card, config, 2, 32 << 20, 32 << 20, base)
    fail("places were made without a backup of userdata")
except app.Failed as err:
    if "Back up userdata first" not in str(err):
        fail(f"without a backup: {err}")
if guid(userdata["number"], "vda") != before:
    fail("a refused change touched the partition table")
by_name = {p.name: p for p in access.partitions()}
if any(n not in by_name for n in app.phone_changes("_b")):
    fail(f"partitions to back up: {sorted(by_name)}")
app.run_backup(job(), device, [by_name[n] for n in app.phone_changes("_b")], os.path.join(base, "Plinius", "before"))
if app.missing_backup(app.list_backups(base), device, app.phone_changes("_b")):
    fail("the backup made in the rescue system does not count")
ok("the phone's own storage changes only once a backup made in this run of the rescue system holds what is overwritten")
app.run_internal_places(job(), device, card, config, 2, 32 << 20, 32 << 20, base)
if not config.get("format_userdata", {}).get(device.serial):
    fail("formatting userdata is not remembered")
card, entries = read()
changed = card["userdata"]
if (changed["start"], changed["number"]) != (userdata["start"], userdata["number"]):
    fail(f"userdata moved: {changed}")
# The places start on a full MiB, so userdata may lose up to that much more
wanted = userdata["sectors"] - 4 * ((32 << 20) // 512)
if not wanted - 2048 < changed["sectors"] <= wanted or (changed["start"] + changed["sectors"]) % 2048:
    fail(f"userdata has the wrong size: {changed['sectors']} from {userdata['sectors']}")
if guid(userdata["number"], "vda") != before:
    fail("userdata lost its identity")
if internal_hashes() != hashes:
    fail("what lies around userdata changed")
ok("userdata became smaller: same start, same identity, in sectors of 4096 bytes; all around it unchanged")
labels = sorted(p["label"] for p in card["parts"] if p.get("internal"))
if labels != ["plinius_dat1", "plinius_dat2", "plinius_sys1", "plinius_sys2"]:
    fail(f"labels: {labels}")
links = sh("ls -l /dev/disk/by-partlabel | grep -c plinius_").strip()
if links != "4":
    fail(f"links: {links}")
ok("four new partitions with their names, and the links the ramdisk finds them by")
internal = [p for p in app.free_pairs(card) if p["system"].get("internal")]
if len(internal) != 2:
    fail(f"free places in the phone: {internal}")
for name, part in (("userdata", {"name": "vda3", "internal": True, "label": "userdata"}),
                   ("a name in the phone", {"name": "vda3", "internal": True, "label": "plinius_sys; reboot"}),
                   ("a disk of the phone without a label", {"name": "vda3"})):
    try:
        app.part_path(part)
        fail(f"{name} could be written")
    except app.Failed:
        pass
ok("nothing else in the phone's storage can be named for writing")

# A system with small blocks would be written and then not start: the disk has sectors of 4096 bytes
tiny = app.SystemImage(small_blocks)
tiny.check()
if tiny.block_size != 1024 or tiny.problem or tiny.place_problem(internal[0]["system"]) == "":
    fail(f"small blocks: {tiny.block_size} {tiny.problem!r}")
try:
    app.run_install(job(), device, card, internal[0], tiny, True, "Tiny", False, base)
    fail("an image with small blocks was written to the phone")
except app.Failed as err:
    if "blocks" not in str(err):
        fail(str(err))
one_k = {"number": 2, "size": 8 << 20, "fs": {"bytes": 1 << 20, "used": 1 << 20, "block_size": 1024}}
if "blocks" not in app.copy_problem(one_k, internal[0]["system"]) or app.copy_problem(one_k, {"number": 8, "size": 32 << 20}):
    fail("moving small blocks into the phone")
ok("a file system with blocks smaller than the phone's sectors is refused there, and still allowed on the card")

# A system on a place in the phone, booted by its name
app.run_install(job(), device, card, internal[0], image, True, "Inside", False, base)
card, entries = read()
inside = next(e for e in card["entries"] if e["title"] == "Inside")
entry = boot_entry(inside["file"])
if "systempart=/dev/disk/by-partlabel/plinius_sys1 " not in entry or "datapart=/dev/disk/by-partlabel/plinius_dat1" not in entry:
    fail(f"entry: {entry}")
if not inside["system"].get("internal") or inside["system"]["label"] != "plinius_sys1":
    fail("the place is not recognized when read again")
if fingerprint(inside["system"]["name"]) != "new-system":
    fail("the system in the phone has wrong contents")
ok("installed in the phone: the entry names it, and it is found again when read")

# From the card into the phone, and between them
card, entries = read()
target = next(p for p in app.free_pairs(card) if p["system"].get("internal"))
source = next(e for e in card["entries"] if e["file"] == "rom2.conf")
app.run_move(job(), device, card, source, target, False, base)
card, entries = read()
entry = boot_entry("rom2.conf")
if "by-partlabel/plinius_sys2" not in entry or fingerprint(target["system"]["name"]) != "new-system" or \
        fingerprint(f"{CARD}p8") != "new-system":
    fail(f"move into the phone: {entry}")
ok("moved from the card into the phone; the card keeps its copy")

# The way back
hashes = internal_hashes()
state = next(s for s in app.load_states(base, device.serial) if s["kind"] == "internal")
if not state["changed"] or not state["done"]:
    fail(f"state: {state}")
config.pop("format_userdata")
card, entries = read()
app.run_restore_table(job(), device, card, config, state)
card, entries = read()
back = card["userdata"]
if (back["start"], back["sectors"]) != (userdata["start"], userdata["sectors"]):
    fail(f"userdata after the way back: {back}")
if [p for p in card["parts"] if p.get("internal")]:
    fail("places still there")
if guid(userdata["number"], "vda") != before or internal_hashes() != hashes:
    fail("the way back changed something else")
if any(e["system"] and e["system"].get("internal") for e in card["entries"]) or "rom2.conf" in entries:
    fail("entries of the systems in the phone stayed")
if not config.get("format_userdata", {}).get(device.serial):
    fail("formatting userdata is not remembered after the way back")
ok("the way back: the old table, the entries out of the menu, formatting remembered, nothing else changed")

# A system instead of Volla OS: all of userdata, the boot partitions of the slot, nothing else
app.PHONE_DATA_MIN = 16 << 20


def phone_image(name, vendor_line=b"bootopt=64S3,32N2,64N2", boot_files=True, crypt=False, plinius=False,
                first_start=False):
    root = os.path.join(image_dir, name)
    os.makedirs(os.path.join(root, "var/lib/lxc/android"))
    os.makedirs(os.path.join(root, "usr/lib"))
    if plinius:
        # As the Volla project builds it: the user with a locked password, kernel and ramdisk for the card
        os.makedirs(os.path.join(root, "etc"))
        with open(os.path.join(root, "etc/shadow"), "w") as f:
            f.write("root:!*:20400::::::\nplinius:!*:20400:0:99999:7:::\nnobody:!*:20400::::::\n")
        os.makedirs(os.path.join(root, "usr/share/plinius-boot"))
        for kernel_file, size in (("zImage", 3 << 20), ("initrd.cpio.gz", 5 << 20)):
            with open(os.path.join(root, "usr/share/plinius-boot", kernel_file), "wb") as f:
                f.write(os.urandom(size))
    if crypt:
        # An image with the optional encryption of the Volla project (plinius-crypt)
        os.makedirs(os.path.join(root, "usr/lib/plinius-crypt"))
        with open(os.path.join(root, app.PHONE_CRYPT_MARK), "w") as f:
            f.write("#!/bin/bash\n")
        # The prompts' table of one language, as plinius-crypt ships them
        os.makedirs(os.path.join(root, "usr/share/plinius-crypt/l10n"))
        with open(os.path.join(root, "usr/share/plinius-crypt/l10n/de"), "w") as f:
            f.write("Encrypt the device?\tGerät verschlüsseln?\n")
    if first_start:
        # Its greeter asks for the first PIN itself (plinius-greeter)
        os.makedirs(os.path.dirname(os.path.join(root, app.FIRST_START_MARK)))
        with open(os.path.join(root, app.FIRST_START_MARK), "w") as f:
            f.write("[Path]\n")
    with open(os.path.join(root, "usr/lib/os-release"), "w") as f:
        f.write('PRETTY_NAME="Phone System"\n')
    with open(os.path.join(root, "marker"), "w") as f:
        f.write("phone-system\n")
    # With a hole in the middle, as the real Android image has
    with open(os.path.join(root, "var/lib/lxc/android/android-rootfs.img"), "wb") as f:
        f.write(os.urandom(65536))
        f.seek(2 << 20)
        f.write(os.urandom(65536))
    files = {}
    if boot_files:
        os.makedirs(os.path.join(root, "usr/share/plinius-boot"), exist_ok=True)
        for part, magic, size in (("boot", b"ANDROID!", 1 << 20), ("vendor_boot", b"VNDRBOOT", 1 << 19), ("init_boot", b"ANDROID!", 1 << 18)):
            data = bytearray(magic + os.urandom(size - len(magic)))
            if part == "vendor_boot":
                data[28:28 + 2048] = vendor_line.ljust(2048, b"\0")
            # A real boot.img is mostly padding: a hole in the file, so that it comes in several pieces
            hole = (64 << 10, size - (64 << 10)) if part == "boot" else None
            if hole:
                data[hole[0]:hole[1]] = bytes(hole[1] - hole[0])
            files[part] = bytes(data)
            with open(os.path.join(root, f"usr/share/plinius-boot/{part}.img"), "wb") as f:
                if hole:
                    f.write(data[:hole[0]])
                    f.seek(hole[1])
                    f.write(data[hole[1]:])
                else:
                    f.write(data)
    path = os.path.join(image_dir, name + ".img")
    subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "4096", "-d", root, path, "48M" if plinius else "32M"], check=True)
    checked = app.SystemImage(path)
    checked.check()
    if checked.problem:
        fail(f"{name}: {checked.problem}")
    return checked, files


def slot_hashes():
    return sh("for n in boot_b vendor_boot_b init_boot_b; do sha256sum /dev/disk/by-partlabel/$n; done").split()[::2]


phone, phone_files = phone_image("phone")
if phone.first_start or not phone_image("phone-first", first_start=True)[0].first_start:
    fail("an image whose greeter asks for the PIN is not told apart")
ok("an image whose greeter asks for the first PIN itself is told apart")
config["rescue_in"] = {device.serial: {"partition": "init_boot_b", "expected": {}}}
if app.phone_install_problem(device, config, phone, 64 << 20):
    fail(f"a good image is refused: {app.phone_install_problem(device, config, phone, 64 << 20)}")
if "does not bring" not in app.phone_install_problem(device, config, phone_image("no-boot", boot_files=False)[0], 64 << 20):
    fail("an image without boot files was accepted")
if "system partition" not in app.phone_install_problem(device, config, phone_image("old-vendor", vendor_line=b"bootopt=1 systempart=/dev/mapper/system")[0], 64 << 20):
    fail("a vendor_boot that names a system partition was accepted")
if "more than the size" not in app.phone_install_problem(device, config, phone, 16 << 20):
    fail("a system smaller than its image was accepted")
for kept in ({}, {"rescue_in": {device.serial: {"partition": "boot_b"}}}):
    if "which partition holds" not in app.phone_install_problem(device, kept, phone, 64 << 20):
        fail(f"accepted without knowing the rescue partition: {kept}")
app.PHONE_DATA_MIN = 1 << 40
before_slot = slot_hashes()
try:
    app.run_install_phone(job(), device, config, phone, 64 << 20, restart=False)
    fail("too little room was accepted")
except app.Failed as err:
    if "too little" not in str(err):
        fail(f"too little room: {err}")
app.PHONE_DATA_MIN = 16 << 20
if slot_hashes() != before_slot:
    fail("a refused install changed the slot")
ok("instead of Volla OS: an image without boot files, one for a system partition, too small a size, an unknown rescue partition and too little room are all refused")

tables = guid(userdata["number"], "vda")
hashes = internal_hashes()
entries_before = {name: boot_entry(name) for name in read()[1]}
cancelling = job()
real_copy = cancelling.copy
sends = []


def cancel_in_system(read_, write, hasher, done, total, text, limit):
    sends.append(text)
    if len(sends) == 1:
        return real_copy(read_, write, hasher, done, total, text, limit)
    end = real_copy(read_, write, hasher, done, total, text, min(limit, 1 << 20))
    cancelling.cancel()
    cancelling.check()
    return end


cancelling.copy = cancel_in_system
try:
    app.run_install_phone(cancelling, device, config, phone, 64 << 20, restart=False)
    fail("not cancelled")
except app.Cancelled:
    pass
if slot_hashes() != before_slot:
    fail("cancelled while writing the system, but the slot changed")
if device.serial not in config["rescue_in"] or "phone_system" in config:
    fail("a cancelled install changed what the app remembers")
if "/run/plinius-phone" in sh("cat /proc/mounts"):
    fail("the data partition stayed mounted after a cancel")
ok("cancelled while writing the system: the slot is untouched, the rescue system still starts, nothing stays mounted")

app.run_install_phone(job(), device, config, phone, 64 << 20, restart=False)
for part, data in phone_files.items():
    if sh(f"head -c {len(data)} /dev/disk/by-partlabel/{part}_b | sha256sum").split()[0] != hashlib.sha256(data).hexdigest():
        fail(f"{part}_b does not hold the image's file")
ok("the next run works: all three boot partitions hold the files from the image")
if device.serial in config["rescue_in"]:
    fail("the app still thinks the rescue system is there")
sh("mkdir -p /run/check && mount -t ext4 /dev/disk/by-partlabel/userdata /run/check")
names = sh("ls -A /run/check").split()
if "rootfs.img" not in names or "android-rootfs.img" not in names or any(n.endswith(".new") for n in names):
    fail(f"files on the data partition: {names}")
if app.PHONE_CRYPT_REQUEST in names:
    fail("an image without plinius-crypt left a request to encrypt")
size = int(sh("stat -c %s /run/check/rootfs.img"))
if size != 64 << 20:
    fail(f"rootfs.img has {size} bytes")
pulled = os.path.join(tmp, "pulled.img")
proc = access.reader("/run/check/rootfs.img", size)
with open(pulled, "wb") as f:
    shutil.copyfileobj(proc.stdout, f)
proc.wait()
with open(pulled, "rb") as f:
    inner = app.Ext4Reader(f)
    if inner.size != 64 << 20 or inner.read_file("marker") != b"phone-system\n":
        fail(f"the system file: {inner.size} {inner.read_file('marker')!r}")
    android = inner.open_file("var/lib/lxc/android/android-rootfs.img")
    data = android.read_exact(android.size)
want = hashlib.sha256(data).hexdigest()
if len(data) != (2 << 20) + 65536 or data[70000:70010] != bytes(10):
    fail("reading a file with a hole")
if sh("sha256sum /run/check/android-rootfs.img").split()[0] != want:
    fail("the Android image next to the system differs from the one inside")
sh("umount /run/check")
ok("on the data partition: the system at the size chosen, grown and readable, and the Android image next to it, hole included")
if guid(userdata["number"], "vda") != tables or internal_hashes() != hashes:
    fail("the partition table or other partitions changed")
if {name: boot_entry(name) for name in read()[1]} != entries_before:
    fail("the boot entries on the card changed")
ok("the partition table, super, nvdata, mrdump and the boot entries on the card stay as they were")

# Leaving a later rescue system must bring back this init_boot, not the one vbmeta describes for Volla OS
inside = app.read_phone_system(device, config)
if inside["kind"] != "plinius":
    fail(f"what the rescue system finds inside the phone after installing: {inside}")
expected = app.known_init_boot(config, device.serial, "b", inside["kind"])
if not expected or not app.matches_hash(phone_files["init_boot"], expected):
    fail(f"the original of the new system: {expected}")
found = app.find_original(device.serial, "init_boot", expected)
if not found or found[0] != phone_files["init_boot"]:
    fail("the init_boot of the new system was not kept")
# A kernel update changes boot_b: still the same system, still its own init_boot
sh("head -c 4096 /dev/urandom | dd of=/dev/disk/by-partlabel/boot_b bs=4096 conv=notrunc 2>/dev/null; sync; echo 3 > /proc/sys/vm/drop_caches")
if app.read_phone_system(device, config)["kind"] != "plinius" or \
        app.known_init_boot(config, device.serial, "b", "plinius") != expected:
    fail("after a kernel update the app no longer knows the system it installed")
# A backup of Volla OS restored: userdata holds Android's folders again, and Volla's init_boot is right
sh("mkfs.ext4 -F -q /dev/disk/by-partlabel/userdata && mkdir -p /run/check && "
   "mount -t ext4 /dev/disk/by-partlabel/userdata /run/check && mkdir /run/check/data /run/check/media "
   "/run/check/misc && umount /run/check")
inside = app.read_phone_system(device, config)
if inside["kind"] != "android" or app.known_init_boot(config, device.serial, "b", "android") is not None:
    fail(f"after Volla OS came back: {inside}")
ok("a later rescue system goes back to this init_boot, also after a kernel update, and to Volla's once a backup "
   "of it was restored")

# An image with plinius-crypt: the phone is to ask once at its first start, the request says "fresh"
crypt_image, _crypt_files = phone_image("phone-crypt", crypt=True)
config["rescue_in"] = {device.serial: {"partition": "init_boot_b", "expected": {}}}
app.run_install_phone(job(), device, config, crypt_image, 64 << 20, restart=False, language="de")
sh("mount -t ext4 /dev/disk/by-partlabel/userdata /run/check")
request = sh(f"cat /run/check/{app.PHONE_CRYPT_REQUEST} 2>/dev/null; true")
beside = sh("cat /run/check/.plinius-crypt-l10n; echo ---; cat /run/check/.plinius-crypt-keyboard; true")
system = sh("m=$(mktemp -d); mount -t ext4 -o ro,loop /run/check/rootfs.img \"$m\"; cat \"$m/etc/locale.conf\" "
            "\"$m/etc/vconsole.conf\" \"$m/etc/X11/xorg.conf.d/00-keyboard.conf\"; umount \"$m\"; rmdir \"$m\"")
sh("umount /run/check")
if request.strip() != "fresh":
    fail(f"the request to encrypt after installing an image with plinius-crypt: {request!r}")
ok("an image with plinius-crypt: the new data partition carries the request \"fresh\", so the phone asks once")
if "LANG=de_DE.UTF-8\nKEYMAP=de-latin1\n" not in system or 'Option "XkbLayout" "de"' not in system:
    fail(f"the language in the system:\n{system}")
if beside != "Encrypt the device?\tGerät verschlüsseln?\n---\n[keyboard]\nlayout=de\n\n[hardware keyboard]\nlayout=de\n":
    fail(f"what the prompt at the first start takes:\n{beside!r}")
ok("the language chosen: in the system as Settings write it, and next to the request for the prompt at the first start")

# Stufe 7: the guided installation (InstallPage): Plinius with its own kernel and a PIN
PIN = app.sha512_crypt("2580")
if PIN != app.sha512_crypt("2580", PIN.split("$")[2]) or not PIN.startswith("$6$"):
    fail(f"the PIN hash: {PIN}")
if app.sha512_crypt("Hello world!", "saltstring") != \
        "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1":
    fail("SHA-512 crypt does not give Drepper's test vector")
if app.pin_problem("12a4", "12a4") == "" or app.pin_problem("1234", "1243") == "" or app.pin_problem("123", "123") == "" \
        or app.pin_problem("2580", "2580"):
    fail("PIN rules")
ok("the PIN: SHA-512 crypt as in the specification, 4 to 12 digits, typed twice")
plinius, plinius_files = phone_image("plinius", plinius=True)
if app.card_image_problem(plinius) or not app.card_image_problem(phone):
    fail("an image without its own kernel must not go onto the card, one with it must")
# Small numbers for a card of 512 MiB; the rules are the same
app.CARD_SYSTEM_ROOM, app.CARD_DATA_MIN, app.CARD_SETTINGS_MIN = 8 << 20, 16 << 20, 16 << 20


def pulled_settings(card_):
    """abm_settings of the card on this PC, read by debugfs: its "ls" goes through a directory as stored,
    as lwext4 in the bootloader does."""
    part = next(p for p in card_["parts"] if p["name"] == card_["settings"])
    out = os.path.join(tmp, "settings.img")
    proc = access.reader(f"/dev/{part['name']}", part["size"])
    with open(out, "wb") as f:
        shutil.copyfileobj(proc.stdout, f)
    proc.wait()
    return out


def stored_order(path):
    listing = subprocess.run(["debugfs", "-R", "ls -p /db/entries", path], capture_output=True, text=True,
                             check=True).stdout
    # /inode/mode/uid/gid/name/size/: regular files only, as the bootloader counts them
    return [f[5] for f in (line.split("/") for line in listing.splitlines()) if len(f) > 6 and f[2].startswith("100")]


def debugfs_cat(path, name):
    return subprocess.run(["debugfs", "-R", f"cat {name}", path], capture_output=True, check=True).stdout


def check_plinius(card_, name, label, tree="plinius"):
    """The entry, its kernel (that of the image made from tree), the number the bootloader starts by itself,
    and the PIN in the system."""
    entries_ = {e["file"]: e for e in card_["entries"]}
    entry_ = entries_.get(name)
    if not entry_ or not entry_["system"] or not entry_["data"]:
        fail(f"{label}: no entry {name}: {list(entries_)}")
    folder = entry_["kernel"]
    text = boot_entry(name)
    for line in (f"linux {folder}/zImage", f"initrd {folder}/initrd.cpio.gz", f"dtb {folder}/dtb.dtb", "xtype UT",
                 f"xpart {entry_['system']['number']}:{entry_['data']['number']}",
                 f"systempart=/dev/{CARD}p{entry_['system']['number']} datapart=/dev/{CARD}p{entry_['data']['number']}"):
        if line not in text:
            fail(f"{label}: {line!r} not in the entry:\n{text}")
    if entry_mode(name) != "1000 600":
        fail(f"{label}: the entry is not as the manager makes them")
    settings = pulled_settings(card_)
    for kernel_file in app.IMAGE_KERNEL:
        with open(os.path.join(image_dir, tree, "usr/share/plinius-boot", kernel_file), "rb") as f:
            if debugfs_cat(settings, f"/{folder}/{kernel_file}") != f.read():
                fail(f"{label}: {folder}/{kernel_file} is not the one from the image")
    order = stored_order(settings)
    last = debugfs_cat(settings, "/db/last_index")
    if len(last) != 128 or last.rstrip(b"\0") != str(order.index(name)).encode() or \
            app.entry_order(access, card_["settings"]) != order:
        fail(f"{label}: last_index {last[:8]!r} for {name} in {order}")
    system = f"/dev/{CARD}p{entry_['system']['number']}"
    shadow = sh(f"m=$(mktemp -d); mount -t ext4 -o ro {system} \"$m\"; cat \"$m/etc/shadow\"; stat -c '%u %a' \"$m/etc/shadow\"; "
                f"cat \"$m/marker\"; umount \"$m\"; rmdir \"$m\"")
    lines = shadow.splitlines()
    if f"plinius:{PIN}:" not in shadow or not lines[0].startswith("root:!*:") or lines[3] != "0 600" or lines[4] != "phone-system":
        fail(f"{label}: the system or its PIN:\n{shadow}")
    return order


card, entries = read()
before = {name: boot_entry(name) for name in entries}
choices = app.card_choices(card, plinius)
if not choices or choices[-1]["kind"] != "fresh" or choices[0]["kind"] not in ("free", "add"):
    fail(f"choices on a card with a boot menu and room: {[c['kind'] for c in choices]}")
ok(f"on a card with a boot menu, first a place where nothing is lost ({choices[0]['kind']}), last setting it up anew")
name = app.run_install_card(job(), device, card, choices[0], plinius, None, PIN, "fr")
card, entries = read()
order = check_plinius(card, name, "into the free space")
if entries[name]["title"] != "Arch Linux":
    fail(f"the title of the new entry: {entries[name]['title']!r}")
ok("the entry is named after what it starts: Arch Linux")
system = entries[name]["system"]["name"]
language = sh(f"m=$(mktemp -d); mount -t ext4 -o ro /dev/{system} \"$m\"; cat \"$m/etc/locale.conf\" \"$m/etc/vconsole.conf\"; "
              "umount \"$m\"; rmdir \"$m\"")
if language != "LANG=fr_FR.UTF-8\nKEYMAP=fr-latin1\n":
    fail(f"the language on the card: {language!r}")
ok("on the card the language is set as well")


def card_data_files(entry_):
    """The names on the data partition of an entry, hidden ones included, and the request in it ("" if none)."""
    part = f"/dev/{entry_['data']['name']}"
    names = sh(f"m=$(mktemp -d); mount -t ext4 -o ro {part} \"$m\"; ls -A \"$m\"; umount \"$m\"; rmdir \"$m\"").split()
    request = sh(f"m=$(mktemp -d); mount -t ext4 -o ro {part} \"$m\"; cat \"$m/{app.PHONE_CRYPT_REQUEST}\" 2>/dev/null; "
                 f"umount \"$m\"; rmdir \"$m\"; true")
    return names, request.strip()


if app.PHONE_CRYPT_REQUEST in card_data_files(entries[name])[0]:
    fail("an image without plinius-crypt left a request to encrypt on the card")
ok("an image without plinius-crypt leaves no request on the card's data partition")
if any(boot_entry(n) != text for n, text in before.items()):
    fail("other entries changed")
ok(f"installed next to the others as {name}: own kernel in its folder, last_index {order.index(name)} as the "
   "bootloader counts, the PIN set, the other entries untouched")

# Once more on the same place: the folder is used again, the entry keeps its name and so its number
again = next(c for c in app.card_choices(card, plinius) if c["kind"] == "replace" and c["place"]["file"] == name)
if app.run_install_card(job(), device, card, again, plinius, None, PIN) != name:
    fail("installing again on its own place gave another entry")
card, entries = read()
if check_plinius(card, name, "again") != order or entries[name]["kernel"] != "plinius":
    fail("installing again changed the order or the kernel folder")
if entries[name]["title"] != "Arch Linux":
    fail(f"installing again renamed the entry: {entries[name]['title']!r}")
ok("installing again on its own place keeps entry, name, number and kernel folder")

# An image with plinius-crypt on the card: the phone is to ask once at its first start, as inside the phone
crypt_card, _crypt_card_files = phone_image("plinius-crypt", plinius=True, crypt=True)
again = next(c for c in app.card_choices(card, crypt_card) if c["kind"] == "replace" and c["place"]["file"] == name)
if app.run_install_card(job(), device, card, again, crypt_card, None, PIN, "de") != name:
    fail("an image with plinius-crypt on its own place gave another entry")
card, entries = read()
check_plinius(card, name, "with plinius-crypt", "plinius-crypt")
names, request = card_data_files(entries[name])
if request != "fresh" or sorted(names) != [".plinius-crypt-keyboard", ".plinius-crypt-l10n", app.PHONE_CRYPT_REQUEST,
                                           "lost+found"]:
    fail(f"the data partition after an image with plinius-crypt: {names} {request!r}")
data_part = f"/dev/{entries[name]['data']['name']}"
beside = sh(f"m=$(mktemp -d); mount -t ext4 -o ro {data_part} \"$m\"; cat \"$m/.plinius-crypt-l10n\"; echo ---; "
            f"cat \"$m/.plinius-crypt-keyboard\"; umount \"$m\"; rmdir \"$m\"")
if beside != "Encrypt the device?\tGerät verschlüsseln?\n---\n[keyboard]\nlayout=de\n\n[hardware keyboard]\nlayout=de\n":
    fail(f"the language of the prompt on the card: {beside!r}")
if sh("findmnt -n /run/plinius-card-data; ls -d /run/plinius-card-data 2>/dev/null; true"):
    fail("the card's data partition is still mounted for the request")
ok("an image with plinius-crypt on the card: its new data partition carries the request \"fresh\" and the language "
   "of the prompt, so the phone asks once at its first start")

# A card from the shop: no boot menu, here not even a partition table
sh(f"sgdisk --zap-all /dev/{CARD} >/dev/null 2>&1; blockdev --rereadpt /dev/{CARD}; plinius-partlinks; true")
card, entries = read()
if card["settings"] or card["parts"] or card["entries"]:
    fail(f"a blank card: {card['parts']} {card['entries']}")
choices = app.card_choices(card, plinius)
if [c["kind"] for c in choices] != ["fresh"]:
    fail(f"choices on a blank card: {choices}")
small = app.SystemImage(plinius.path)
small.check()
small.size = 1 << 30
if app.card_choices(card, small):
    fail("an image too large for the card was offered")
name = app.run_install_card(job(), device, card, choices[0], plinius, None, PIN)
card, entries = read()
gpt = sh(f"sgdisk -p /dev/{CARD}")
if [p["number"] for p in card["parts"]] != [1, 2, 3] or card["settings"] != f"{CARD}p1" or "abm_settings" not in gpt \
        or not re.search(r"^\s*1\s.*\s8301\s", gpt, re.M) or not re.search(r"^\s*2\s.*\s8305\s", gpt, re.M) \
        or not re.search(r"^\s*3\s.*\s8300\s", gpt, re.M):
    fail(f"the new partition table:\n{gpt}")
if card["parts"][0]["start"] != 2048 or card["parts"][0]["size"] != 16 << 20:
    fail(f"abm_settings: {card['parts'][0]}")
if card["free"] > 2 << 20:
    fail(f"room left unused: {card['free']}")
order = check_plinius(card, name, "a card set up anew")
settings = pulled_settings(card)
if order != ["real.conf", name] or debugfs_cat(settings, "/db/db.conf") != app.DB_CONF.encode() or \
        debugfs_cat(settings, "/db/entries/real.conf") != app.REAL_ENTRY.encode() or \
        not entries["real.conf"]["own_system"] or entries["real.conf"]["title"] != "VollaOS":
    fail(f"the boot menu of a new card: {order}")
ok("a blank card is set up as the Volla Boot Manager does: abm_settings, db.conf, VollaOS first, Plinius second "
   "and started by itself; all of the rest of the card is used")
features = sh(f"dumpe2fs -h /dev/{CARD}p1 2>/dev/null | sed -n 's/^Filesystem features://p'").split()
unknown = set(features) - {"has_journal", "ext_attr", "resize_inode", "dir_index", "filetype", "extent", "64bit",
                           "flex_bg", "sparse_super", "large_file", "huge_file", "dir_nlink", "extra_isize",
                           "metadata_csum", "orphan_file", "needs_recovery", "sparse_super2"}
if unknown:
    fail(f"abm_settings has features lwext4 may not know: {unknown}")
ok("abm_settings has only features the bootloader's lwext4 reads")

# Instead of Volla OS, with the PIN in the system file
config["rescue_in"] = {device.serial: {"partition": "init_boot_b", "expected": {}}}
app.run_install_phone(job(), device, config, plinius, 64 << 20, restart=False, pin_hash=PIN)
shadow = sh("m=$(mktemp -d); mount -t ext4 /dev/disk/by-partlabel/userdata /run/check; mount -t ext4 -o ro,loop "
            "/run/check/rootfs.img \"$m\"; cat \"$m/etc/shadow\"; umount \"$m\"; rmdir \"$m\"; umount /run/check")
if f"plinius:{PIN}:" not in shadow:
    fail(f"the PIN in the system on the phone itself:\n{shadow}")
ok("instead of Volla OS: the PIN is in the system file as well")
app.save_config = real_save

shutil.rmtree(tmp, ignore_errors=True)
print("All checks of the systems on the card passed", file=sys.stderr)
