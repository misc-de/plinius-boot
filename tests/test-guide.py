#!/usr/bin/env python3
"""Tests the logic of the guided flow without a phone and without a window: the tool check, what the
app makes of a phone, and how it reads the memory card. card-plinius.txt is the real output of
CARD_SCRIPT on a Plinius with Ubuntu Touch and Arch on its card."""
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import types
import urllib.error

sys.dont_write_bytecode = True
here = pathlib.Path(__file__).resolve().parent
tmp = pathlib.Path(tempfile.mkdtemp())
for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
    os.environ[variable] = str(tmp)
os.environ["LC_ALL"] = "C.UTF-8"
os.environ["LANGUAGE"] = "en"

loader = importlib.machinery.SourceFileLoader("plinius_boot", str(here.parent / "plinius-boot"))
app = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(app)
# Nothing here may reach real data
assert app.CONFIG_HOME == str(tmp) and app.DATA_HOME == str(tmp), "not in the throw-away folder"


def check(name, condition):
    if not condition:
        sys.exit(f"FAILED: {name}")
    print(f"  ok: {name}")


# The memory card
card = app.parse_card((here / "card-plinius.txt").read_text())
check("the card and its size", card["disk"] == "mmcblk0" and card["size"] == 499744768 * 512)
check("five partitions in order", [p["number"] for p in card["parts"]] == [1, 2, 3, 4, 5])
check("a full card has no space worth using", card["largest_free"] < 4 << 20)
by_file = {e["file"]: e for e in card["entries"]}
check("Volla OS is the phone's own system", by_file["real.conf"]["own_system"] and not by_file["real.conf"]["system"])
ut, arch = by_file["rom0.conf"], by_file["rom1.conf"]
check("xpart gives system and data", (ut["system"]["number"], ut["data"]["number"]) == (2, 3)
      and (arch["system"]["number"], arch["data"]["number"]) == (4, 5))
check("Ubuntu Touch with its version", ut["system"]["system"]["name"] == "Ubuntu Touch"
      and ut["system"]["system"]["version"] == "24.04-2.x")
check("the running system is marked", arch["system"]["running"] and not ut["system"]["running"])
check("a data partition holds no system", not arch["data"]["system"])

check("the partition with the boot entries", card["settings"] == "mmcblk0p1")
check("GPT types", card["parts"][2]["type"] == app.GPT_HOME and card["parts"][3]["type"] != app.GPT_HOME)
check("the file systems", card["parts"][1]["fs"]["bytes"] == 1324955 * 4096 and "fs" not in card["parts"][0])
# As measured on the phone: Ubuntu Touch switches gpt-auto off, Arch does not, so /home is on p3
check("Arch keeps /home on the data of Ubuntu Touch", app.home_users(card) == {3: ["Arch Linux"]})
check("the warning is on the place of Ubuntu Touch", app.place_warnings(card, ut) and not app.place_warnings(card, arch))
check("a kernel folder in use by another system", app.uses_kernel_of(card, ut) == ["Arch Linux"] and not app.uses_kernel_of(card, arch))
off = dict(arch, options=arch["options"] + " systemd.gpt_auto=0")
check("systemd.gpt_auto=0 ends it", app.home_users(dict(card, entries=[ut, off])) == {})
check("two places, nothing free", [e["file"] for e in app.card_places(card)] == ["rom0.conf", "rom1.conf"]
      and app.free_pairs(card) == [])
big, small = {"number": 5, "size": 200 << 30, "fs": {"bytes": 192 << 30, "used": 4 << 30}}, {"number": 3, "size": 16 << 30}
check("a big empty file system is made smaller first", app.needs_shrinking(big, small))
check("it fits then", not app.copy_problem(big, small))
filled = dict(big, fs={"bytes": 192 << 30, "used": 100 << 30})
check("too much data does not", "does not fit" in app.copy_problem(filled, small))
check("what cannot be read goes only as a whole", app.copy_problem({"number": 5, "size": 200 << 30}, small)
      and not app.copy_problem({"number": 3, "size": 16 << 30}, dict(big)))
check("a full file system in the same size stays", not app.needs_shrinking(
    {"size": 16 << 30, "fs": {"bytes": 16 << 30, "used": 12 << 30}}, {"size": 17 << 30}))
text = app.entry_text(arch, title="New\nline", system={"number": 6, "name": "mmcblk0p6"}, data={"number": 7, "name": "mmcblk0p7"})
check("an entry keeps the rest of its lines", "rom0/zImage" in text and "xpart 6:7\n" in text
      and "systempart=/dev/mmcblk0p6 datapart=/dev/mmcblk0p7" in text and "bootopt=" in text)
check("a title cannot add a line", "title Newline\n" in text and text.count("\n") == len(arch["raw"]))
for name in ("mmcblk0p1;reboot", "../sda", "sda1", "mmcblk0"):
    try:
        app.part_path({"name": name})
        check(f"{name} is refused", False)
    except app.Failed:
        pass
check("only partitions of the card go into a command", app.part_path({"name": "mmcblk0p12"}) == "/dev/mmcblk0p12")

userdata = card["userdata"]
check("userdata as measured", (userdata["name"], userdata["disk"], userdata["sector_size"], userdata["count"]) == ("sdc76", "sdc", 4096, 78))
check("room for places, with a minimum for Volla OS", app.internal_room(card) == userdata["size"] - app.USERDATA_MIN)
check("no place in the phone's storage yet", not [p for p in card["parts"] if p.get("internal")])
inner = app.parse_card("D\tmmcblk0\t100000\nU\tsdc76\tsdc\t8\t80\t4096\t78\nI\tsdc79\tplinius_sys1\t100\t200\n"
                       "I\tsdc80\tuserdata\t100\t200\nI\tsda1\tplinius_sys2;reboot\t100\t200\n"
                       "E\tr.conf\ttitle R\nE\tr.conf\toptions a systempart=/dev/disk/by-partlabel/plinius_sys1 b\n")
check("in the phone's storage only the app's own names count", [p["name"] for p in inner["parts"]] == ["sdc79"])
check("an entry finds its system by that name", inner["entries"][0]["system"]["name"] == "sdc79")
check("a place in the phone can be written", app.part_path(inner["parts"][0]) == "/dev/sdc79")
check("it boots by name", app.boot_path(inner["parts"][0]) == "/dev/disk/by-partlabel/plinius_sys1")
check("on the card by device", app.boot_path({"name": "mmcblk0p4"}) == "/dev/mmcblk0p4")
check("partitions of different media are never a pair", app.free_pairs(dict(card, entries=[], settings="", parts=[
    {"name": "mmcblk0p6", "number": 6}, {"name": "sdc79", "number": 79, "internal": True}])) == [])
check("a shrink needs a readable file system", app.card_resize_problem({"number": 1, "size": 5}, 1))
check("a shrink needs to make it smaller", app.card_resize_problem(big, 300 << 30))
check("a shrink cannot go below what is used", "cannot become" in app.card_resize_problem(big, 3 << 30))
check("a shrink within reason", not app.card_resize_problem(big, 8 << 30))

check("no card", app.parse_card("") is None)
hostile = "\n".join((
    "D\t../../etc\t100",                     # a path as the disk
    "D\tmmcblk0\t100000",
    "P\tmmcblk0p1;reboot\t2048\t100",        # a command in the name
    "P\tmmcblk0p2\t2048\tmany",              # not a number
    "P\tmmcblk0p3\t2048\t1000",
    "O\tmmcblk0p9\tNAME=unknown",            # a partition that was not listed
    "E\t../../evil.conf\ttitle evil",        # a path as the file name
    "E\trom.conf\ttitle " + "x" * 500,       # far too long
    "E\trom.conf\txpart 3:99",               # a partition that does not exist
    "E\trom.conf\txtype UT",
))
bad = app.parse_card(hostile)
check("wrong lines are dropped", [p["number"] for p in bad["parts"]] == [3] and bad["disk"] == "mmcblk0")
check("wrong entries are dropped", [e["file"] for e in bad["entries"]] == ["rom.conf"])
check("long titles are cut", len(bad["entries"][0]["title"]) <= 60)
check("an unknown partition is no partition", bad["entries"][0]["data"] is None
      and bad["entries"][0]["system"]["number"] == 3)

# Free areas
check("an empty disk is free up to the backup GPT", app.free_areas(100000, []) == [(2048, 100000 - 2048 - 34)])
check("gaps in any order", app.free_areas(100000, [(50000, 1000), (2048, 1000)]) ==
      [(3048, 46952), (51000, 100000 - 51000 - 34)])
check("no gap", app.free_areas(10034, [(2048, 8000)]) == [])

# The boot menu and how the card is divided, as the phone page shows them
layout = {"android": [], "storage": [], "card": card, "complete": True}
check("the boot menu in the order of its files", [app.entry_title(e) for e in app.boot_entries(layout)]
      == ["VollaOS", "Ubuntu Touch", "Arch Linux"])
check("without a card only the phone's own system", app.boot_entries(dict(layout, card=None)) == [app.OWN_ENTRY]
      and app.entry_title(app.OWN_ENTRY) == "Volla OS")
check("where an entry is", app.entry_internal(by_file["real.conf"]) and not app.entry_internal(ut)
      and app.entry_summary(layout, arch) == "Arch Linux ARM · Memory card")
segments = app.card_segments(card)
check("the card from start to end", [s[2]["number"] for s in segments if s[2]] == [1, 2, 3, 4, 5]
      and [s[0] for s in segments] == sorted(s[0] for s in segments))
check("alignment gaps are no free space", all(s[1] * app.SECTOR >= app.FREE_MIN for s in segments if not s[2]))
parts = {p["number"]: p for p in card["parts"]}
check("what each partition is for", app.part_role(card, parts[1]) == ("settings", None)
      and app.part_role(card, parts[4]) == ("system", arch) and app.part_role(card, parts[3]) == ("data", ut))
check("the facts of a partition", app.part_text(parts[3]).startswith("mmcblk0p3 · ") and "/home" in app.part_text(parts[3])
      and "running now" in app.part_text(parts[4]))
mine = app.Backup("/nowhere", {"device": {"serial": "A1", "name": "Volla Phone Plinius"}})
other = app.Backup("/nowhere", {"device": {"serial": "B2", "name": "Volla Phone Plinius"}})
phone = app.Device("ssh:x", "ssh", info={"serial": "A1"})
check("only the backups of this phone", app.belongs_to(mine, phone) and not app.belongs_to(other, phone))
known = app.offline_device("A1", "Some phone", {"known": {"A1": "volla-plinius"}})
check("a known phone keeps its profile", known.profile.id == "volla-plinius" and known.mode_text() == "Not connected"
      and not known.can_read)
check("an unknown one its name", app.offline_device("C3", "Pixel", {}).title() == "Pixel")

# The tool check, with programs that behave as told
bin_dir = tmp / "bin"
bin_dir.mkdir()


def fake(name, body):
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


old_path = os.environ["PATH"]
os.environ["PATH"] = str(bin_dir)
for tool in ("adb", "fastboot", "ssh", "ip"):
    fake(tool, "exit 0")
fake("ssh-keygen", "exit 1")     # answers its help with an error, which is fine
check("all tools work", not any(problem for _n, _p, problem in app.check_tools()))
(bin_dir / "adb").unlink()
fake("fastboot", 'echo "libfoo.so missing" >&2; exit 1')
(bin_dir / "ip").write_text("#!/bin/sh\n")
(bin_dir / "ip").chmod(0o644)    # there, but may not be run
tools = {name: problem for name, _purpose, problem in app.check_tools()}
check("a missing program is named", tools["adb"] == "Not installed.")
check("a broken program says why", "libfoo.so missing" in tools["fastboot"])
check("a program that may not be run is named", tools["ip"] in ("Cannot be run.", "Not installed."))
check("the others are fine", not tools["ssh"] and not tools["ssh-keygen"])
os.environ["PATH"] = old_path

# What to install
release = tmp / "os-release"
for content, family in (('ID=manjaro\nID_LIKE=arch\n', "arch"), ('ID=arch\n', "arch"),
                        ('ID=ubuntu\nID_LIKE="debian"\n', "debian"), ('ID="rocky"\nID_LIKE="rhel centos fedora"\n', "fedora"),
                        ('ID=plan9\n', "")):
    release.write_text(content)
    check(f"{content.split()[0]} is {family or 'not known'}", app.system_family(str(release)) == family)
check("no file", app.system_family(str(tmp / "missing")) == "")
check("packages only once", app.install_hint(["adb", "fastboot", "ip"], "arch") == "sudo pacman -S android-tools iproute2")
check("no hint for an unknown system", app.install_hint(["adb"], "") == "")

# A phone this user may not open
sysfs, dev = tmp / "sys", tmp / "dev"
(sysfs / "real/1-2/1-2:1.0").mkdir(parents=True)
for name, value in (("bInterfaceClass", "ff"), ("bInterfaceSubClass", "42"), ("bInterfaceProtocol", "03")):
    (sysfs / "real/1-2/1-2:1.0" / name).write_text(value + "\n")
(sysfs / "real/1-2/busnum").write_text("1\n")
(sysfs / "real/1-2/devnum").write_text("7\n")
(sysfs / "bus").mkdir()
(sysfs / "bus/1-2:1.0").symlink_to(sysfs / "real/1-2/1-2:1.0")
(dev / "001").mkdir(parents=True)
node = dev / "001/007"
node.write_text("")
node.chmod(0o600)
check("a phone the user may open", not app.usb_blocked(str(sysfs / "bus"), str(dev)))
node.chmod(0o000)
if os.getuid() != 0:
    check("a phone the user may not open", app.usb_blocked(str(sysfs / "bus"), str(dev)))
(sysfs / "real/1-2/1-2:1.0/bInterfaceProtocol").write_text("00\n")
check("another device does not count", not app.usb_blocked(str(sysfs / "bus"), str(dev)))


# What the app makes of a phone
def device(mode, info=None, profile=None, vars=None, access=True):
    plinius = next(p for p in app.PROFILES if p.id == profile) if profile else None
    return types.SimpleNamespace(mode=mode, info=info or {}, profile=plinius, rescue=(info or {}).get("os_id") == app.RESCUE_ID,
                                 access=types.SimpleNamespace(vars=vars or {}) if access else None)


check("Plinius Plus is a Plinius", app.is_plinius(device("ssh", profile="volla-plinius-plus")) is True)
check("Plinius is a Plinius", app.is_plinius(device("adb", profile="volla-plinius")) is True)
check("FLX1 is not", app.is_plinius(device("ssh", profile="furilabs-flx1")) is False)
check("an unknown phone in its system is not", app.is_plinius(device("adb", {"model": "Pixel"})) is False)
check("in fastboot it may be not known yet", app.is_plinius(device("fastboot")) is None)
check("fastboot says unlocked", app.bootloader_open(device("fastboot", vars={"unlocked": "yes"})) is True)
check("fastboot says locked", app.bootloader_open(device("fastboot", vars={"unlocked": "no"})) is False)
check("fastboot says nothing", app.bootloader_open(device("fastboot")) is None)
check("orange is unlocked", app.bootloader_open(device("ssh", {"verified_boot": "orange"})) is True)
check("green is locked", app.bootloader_open(device("adb", {"verified_boot": "green"})) is False)
check("yellow is locked", app.bootloader_open(device("adb", {"verified_boot": "yellow"})) is False)
check("the property helps out", app.bootloader_open(device("adb", {"flash_locked": "0"})) is True
      and app.bootloader_open(device("adb", {"flash_locked": "1"})) is False)
check("nothing known", app.bootloader_open(device("ssh")) is None)
check("the rescue system needs an open bootloader", app.bootloader_open(device("ssh", {"os_id": app.RESCUE_ID})) is True)

# Plus and Plinius are one phone to the software: Ubuntu Touch and Arch report a Plus as "Plinius"
plus, plain = (next(p for p in app.PROFILES if p.id == i) for i in ("volla-plinius-plus", "volla-plinius"))
check("both start the rescue system the same way", plain.rescue_partition == plus.rescue_partition == "init_boot"
      and plain.ota_index == plus.ota_index)
check("a Plus reads as Plus, under Arch as Plinius", app.find_profile({"model": "Volla Phone Plinius Plus"}) is plus
      and app.find_profile({"model": "Volla Phone Plinius"}) is plain)
check("a backup of the Plus fits, whatever the system says", app.same_profile("volla-plinius-plus", "volla-plinius")
      and app.same_profile("volla-plinius", "volla-plinius-plus") and app.same_profile("furilabs-flx1", "furilabs-flx1"))
check("another phone does not fit", not app.same_profile("volla-plinius", "furilabs-flx1")
      and not app.same_profile("furilabs-flx1", "furilabs-flx1s"))

config = {}
scanner = app.Scanner(config, lambda: None)
seen = types.SimpleNamespace(profile=plus, serial="S1", info={})
scanner.remember(seen)
check("the Plus is remembered", config["known"]["S1"] == "volla-plinius-plus")
under_arch = types.SimpleNamespace(profile=plain, serial="S1", info={})
scanner.remember(under_arch)
check("Arch does not make it a plain Plinius", config["known"]["S1"] == "volla-plinius-plus" and under_arch.profile is plus)
in_fastboot = types.SimpleNamespace(profile=None, serial="S1", info={})
scanner.remember(in_fastboot)
check("in fastboot it is known by its serial number", in_fastboot.profile is plus)
config["known"]["S2"] = "volla-plinius"
upgraded = types.SimpleNamespace(profile=plus, serial="S2", info={})
scanner.remember(upgraded)
check("Volla OS can correct a plain Plinius", config["known"]["S2"] == "volla-plinius-plus")

# Formatting userdata on the way out of the rescue system, only when the table was changed
calls = []
real_run, real_save = app.run, app.save_config
app.save_config = lambda _c: None
fb = types.SimpleNamespace(fb=lambda *a: ["fastboot", *a])
app.run = lambda cmd, **_k: (calls.append(cmd) or (0, "", ""))
app.format_userdata_if_needed(fb, {"format_userdata": {"S2": True}}, "S1", lambda _t: None)
check("another phone is not formatted", calls == [])
config = {"format_userdata": {"S1": True}}
app.format_userdata_if_needed(fb, config, "S1", lambda _t: None)
check("the changed one is, once", calls == [["fastboot", "-w"]] and "S1" not in config["format_userdata"])
app.run = lambda cmd, **_k: (1, "", "no")
config = {"format_userdata": {"S1": True}}
try:
    app.format_userdata_if_needed(fb, config, "S1", lambda _t: None)
    check("a failure is reported", False)
except app.Failed:
    check("a failure is reported and remembered", config["format_userdata"]["S1"])
app.run, app.save_config = real_run, real_save

info = app.parse_info("cmdline=x androidboot.verifiedbootstate=orange\nbootconfig=\nflashlocked=0\nos=Arch\nosid=arch\n")
check("the cmdline tells the Verified Boot state", info["verified_boot"] == "orange" and info["flash_locked"] == "0")
info = app.parse_info("cmdline=x\nbootconfig=androidboot.flash.locked=1\nverifiedboot=green\n")
check("the property and bootconfig tell it too", info["verified_boot"] == "green" and info["flash_locked"] == "1")
check("the start of the system is told apart", app.parse_info("bootid=0f3c\n")["boot_id"] == "0f3c"
      and app.parse_info("")["boot_id"] == "")

# Installing on the phone itself waits for a backup of what it overwrites, made in this run of the rescue system
store = tmp / "backups"


def make_backup(stamp, partitions, serial="S1", boot_id="run-1", sign=True):
    folder = store / f"Plinius_{serial}" / stamp
    folder.mkdir(parents=True)
    entries = []
    for name, extra in partitions.items():
        (folder / f"{name}.img.zst").write_bytes(b"x")
        entries.append({"name": name, "size": 1, "sha256": "0" * 64, **extra})
    data = {"format": 1, "created": f"2026-11-21T{stamp}:00+01:00", "complete": True, "partitions": entries,
            "device": {"serial": serial, "boot_id": boot_id, "os_id": app.RESCUE_ID}}
    if sign:
        app.sign_manifest(data)
    (folder / app.MANIFEST).write_text(json.dumps(data))
    return folder


names = app.phone_changes("_b")
check("what an install on the phone overwrites", names == ["boot_b", "vendor_boot_b", "init_boot_b", "userdata"])
phone = types.SimpleNamespace(rescue=True, serial="S1", info={"serial": "S1", "boot_id": "run-1", "os_id": app.RESCUE_ID})
missing = lambda device=phone, empty=(): app.missing_backup(app.list_backups(str(store)), device, names, empty)
check("without a backup everything is missing", missing() == names)
make_backup("08:00", {"boot_b": {}, "vendor_boot_b": {}, "init_boot_b": {"replaced": "update server"}})
check("a backup of some partitions leaves the rest", missing() == ["userdata"])
check("userdata erased by unlocking needs none", missing(empty=["userdata"]) == [])
make_backup("08:10", {"userdata": {}}, boot_id="run-0")
check("a backup from an earlier start does not count", missing() == ["userdata"])
make_backup("08:20", {"userdata": {}}, serial="S9")
check("nor one of another phone", missing() == ["userdata"])
forged = make_backup("08:30", {"userdata": {}}, sign=False)
check("nor one this computer did not make", missing() == ["userdata"])
signed = make_backup("08:40", {"userdata": {}})
check("several backups add up", missing() == [])
(signed / "userdata.img.zst").unlink()
check("an image that is gone does not count", missing() == ["userdata"])
make_backup("08:50", {"userdata": {}, "init_boot_a": {}, "super": {}})
check("a backup of the whole phone counts", missing() == [])
in_system = types.SimpleNamespace(rescue=False, serial="S1", info={"serial": "S1", "boot_id": "run-1"})
check("outside the rescue system nothing counts", missing(in_system) == names)
later = types.SimpleNamespace(rescue=True, serial="S1", info={"serial": "S1", "boot_id": "run-2", "os_id": app.RESCUE_ID})
check("after the next start nothing counts", missing(later) == names)
unknown = types.SimpleNamespace(rescue=True, serial="S1", info={"serial": "S1", "os_id": app.RESCUE_ID})
check("without knowing the start nothing counts", missing(unknown) == names)
shutil.rmtree(store)
make_backup("09:00", {"boot_b": {}, "vendor_boot_b": {}, "init_boot_b": {"rescue": True}, "userdata": {}})
check("the rescue system in init_boot is no backup of it", missing() == ["init_boot_b"])
check("making places needs userdata only", app.missing_backup(app.list_backups(str(store)), phone,
                                                              app.INTERNAL_CHANGES) == [])
check("the language of the PC, if Plinius speaks it", app.default_language(["de_AT.UTF-8", "de_AT", "de", "C"]) == "de"
      and app.default_language(["pt_BR.UTF-8"]) == "pt" and app.default_language(["sr@latin", "C"]) == "en")
check("every language has its keyboard", all(len(e) == 5 and e[2].endswith(".UTF-8") for e in app.SYSTEM_LANGUAGES)
      and len({e[0] for e in app.SYSTEM_LANGUAGES}) == 9)
check("the prompt's keyboard as look-files writes it", app.unl0kr_keyboard("de-latin1") ==
      "[keyboard]\nlayout=de\n\n[hardware keyboard]\nlayout=de\n"
      and "layout=us,ru\noptions=grp:alt_shift_toggle" in app.unl0kr_keyboard("ru")
      and app.unl0kr_keyboard("pl2") == "" and app.unl0kr_keyboard("us") == "")
scripts = []
writer = types.SimpleNamespace(run_root=lambda script, timeout=0: (scripts.append(script) or (0, "", "")))
for bad in (("xx", "/dev/sda1", ""), ("de", "/dev/sda1;reboot", ""), ("de", "/dev/sda1", "/run/x y")):
    try:
        app.set_language(writer, bad[1], bad[0], beside=bad[2])
        check(f"refused: {bad}", False)
    except app.Failed:
        pass
check("an unknown language or a strange path is refused, nothing runs", scripts == [])
app.set_language(writer, "/dev/sda1", "en", beside="/run/plinius-phone")
check("English: the files, no keyboard for the prompt", "LANG=en_US.UTF-8" in scripts[0]
      and ".plinius-crypt-keyboard" not in scripts[0] and "l10n/en" in scripts[0])
app.set_language(writer, "/dev/sda1", "fr")
check("without a request nothing beside it", "LANG=fr_FR.UTF-8" in scripts[1] and ".plinius-crypt" not in scripts[1])
check("the message names what is missing", "init_boot_b, userdata" in app.backup_first(["init_boot_b", "userdata"]))
shutil.rmtree(store)

# The guided installation: choices on the card, sizes, the download and the order of the boot entries
class Img:
    def __init__(self, size):
        self.size, self.problem = size, ""

    def place_problem(self, part):
        return "" if part["size"] >= self.size else "too small"


image8 = Img(8 << 30)
check("a 16 GB card is enough for an image of 8 GiB", app.card_size_hint(app.card_need(image8)) == "16 GB")
card = app.parse_card((here / "card-plinius.txt").read_text())
kinds = [(c["kind"], c.get("place", {}).get("file")) for c in app.card_choices(card, image8)]
check("a full card with a boot menu: instead of a system, or anew", kinds == [
    ("replace", "rom0.conf"), ("replace", "rom1.conf"), ("fresh", None)])
choices = app.card_choices(card, image8)
check("the warning goes with the place of Ubuntu Touch", choices[0]["warnings"] and not choices[1]["warnings"])
check("anew: abm_settings at most 4 GiB, the system 40 GiB",
      choices[2]["settings"] == 4 << 30 and choices[2]["system"] == 40 << 30)
check("an image larger than a place is not offered there",
      [c["kind"] for c in app.card_choices(card, Img(12 << 30))] == ["replace", "fresh"])


def blank(sectors):
    return dict(card, settings="", entries=[], parts=[], sectors=sectors, size=sectors * 512,
                free=sectors * 512, largest_free=sectors * 512)


small = app.card_choices(blank(15_931_539_456 // 512), image8)
check("a 16 GB card from the shop: set up anew, abm_settings 512 MiB, the system the image and 2 GiB",
      [c["kind"] for c in small] == ["fresh"] and small[0]["settings"] == 512 << 20 and small[0]["system"] == 10 << 30)
check("an 8 GB card is too small", app.card_choices(blank(7_948_206_080 // 512), image8) == [])
roomy = dict(card, largest_free=100 << 30)
first = app.card_choices(roomy, image8)[0]
check("free space on a card with a boot menu comes first, nothing is lost there",
      first["kind"] == "add" and first["system"] == 40 << 30 and first["system"] + first["data"] == (100 << 30) - (1 << 20))
check("a kernel folder of its own", app.kernel_folder(card) == "plinius"
      and app.kernel_folder(dict(card, entries=card["entries"] + [{"file": "plinius.conf", "kernel": "plinius"}])) == "plinius2")
by_file = {e["file"]: e for e in card["entries"]}
text = app.card_entry_text("Plinius\nlinux evil", "plinius", by_file["rom0.conf"]["system"], by_file["rom0.conf"]["data"])
check("the entry: own kernel, the manager's options, no line from the title", text.splitlines() == [
    "title Pliniuslinux evil", "linux plinius/zImage", "initrd plinius/initrd.cpio.gz", "dtb plinius/dtb.dtb",
    "options " + app.ENTRY_OPTIONS + " systempart=/dev/mmcblk0p2 datapart=/dev/mmcblk0p3", "xtype UT", "xpart 2:3"])

check("SHA-512 crypt gives Drepper's test vector", app.sha512_crypt("Hello world!", "saltstring") ==
      "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1")
check("SHA-512 crypt with a longer salt and PIN", app.sha512_crypt("0" * 70, "XyZ./0123456789A") ==
      "$6$XyZ./0123456789A$VhCAK1vhMiMnE0ucXDda5dJuwjp9WS31fJC/PvNxPkv1SjpxCRgwkImP4CCoBxJK4JCr0rX8QS7uyl0voEkUQ0")
fresh = app.sha512_crypt("2580")
check("a new salt each time", fresh != app.sha512_crypt("2580") and fresh == app.sha512_crypt("2580", fresh.split("$")[2]))
check("PINs", not app.pin_problem("2580", "2580") and app.pin_problem("25 80", "25 80") and app.pin_problem("258", "258")
      and app.pin_problem("2580", "2581") and app.pin_problem("1" * 13, "1" * 13))


class FakeResponse(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


served = {"json": b"", "data": b"", "range": True, "requests": []}


def fake_urlopen(request, timeout=0):
    url = request if isinstance(request, str) else request.full_url
    headers = {} if isinstance(request, str) else dict(request.header_items())
    served["requests"].append((url, headers.get("Range")))
    if url.endswith(".json"):
        if served["json"] is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return FakeResponse(served["json"])
    data = served["data"]
    if headers.get("Range") and served["range"]:
        start = int(headers["Range"][6:-1])
        return FakeResponse(data[start:], 206)
    return FakeResponse(data)


real_urlopen = app.urllib.request.urlopen
app.urllib.request.urlopen = fake_urlopen
index = "https://plinius.example/images/latest.json"
good = {"version": "2026.11.20", "url": "https://plinius.example/images/plinius-1.img.zst", "size": 3, "sha256": "0" * 64}
served["json"] = json.dumps(good).encode()
check("the newest image on the server", app.latest_image(index)["name"] == "plinius-1.img.zst")
for bad in ({"url": "http://plinius.example/images/plinius-1.img.zst"}, {"url": "https://elsewhere/plinius-1.img.zst"},
            {"url": "https://plinius.example/images/..img"}, {"url": "https://plinius.example/x/evil.sh"},
            {"size": "3"}, {"size": 0}, {"sha256": "ABC"}):
    served["json"] = json.dumps(dict(good, **bad)).encode()
    try:
        app.latest_image(index)
        check(f"refused: {bad}", False)
    except app.Failed:
        pass
check("another server, plain http, a strange name, size or checksum: all refused", True)
served["json"] = None
check("nothing published yet", app.latest_image(index) is None)


class Quiet:
    def progress(self, *_a):
        pass

    def add_line(self, *_a):
        pass


app.GLib.idle_add = lambda *_a: None
folder = tmp / "images"
payload = os.urandom(9 << 20)
info = {"name": "plinius-1.img.zst", "url": "https://plinius.example/images/plinius-1.img.zst",
        "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
served["data"], served["requests"] = payload, []
path = app.download_image(app.Job(Quiet()), info, str(folder))
check("downloaded and checked", open(path, "rb").read() == payload and not os.path.exists(path + ".part")
      and oct(os.stat(path).st_mode & 0o777) == "0o600")
served["requests"] = []
check("the second time from the disk", app.download_image(app.Job(Quiet()), info, str(folder)) == path
      and served["requests"] == [])
os.remove(path)
with open(path + ".part", "wb") as f:
    f.write(payload[:5 << 20])
served["requests"] = []
app.download_image(app.Job(Quiet()), info, str(folder))
check("a stopped download goes on where it stopped", served["requests"][0][1] == f"bytes={5 << 20}-"
      and open(path, "rb").read() == payload)
os.remove(path)
with open(path + ".part", "wb") as f:
    f.write(payload[:5 << 20])
served["range"] = False
app.download_image(app.Job(Quiet()), info, str(folder))
check("a server without ranges sends it all again", open(path, "rb").read() == payload)
os.remove(path)
# Always another last byte: payload is random, so a fixed one could be the same
served["data"] = payload[:-1] + bytes([payload[-1] ^ 0xFF])
try:
    app.download_image(app.Job(Quiet()), info, str(folder))
    check("a damaged download is refused", False)
except app.Failed:
    check("a damaged download is refused and deleted", not os.listdir(folder))
check("only complete images count", app.downloaded_images(str(folder)) == [])

# A new image a day: the folder keeps the new one and the one before it, nothing the app did not download
served["data"], served["range"] = payload, True
for n, name in enumerate(["plinius-old1.img.zst", "plinius-old2.img.zst", "plinius-old3.img.zst"]):
    (folder / name).write_bytes(b"old")
    os.utime(folder / name, (1_000_000 + n, 1_000_000 + n))
(folder / "plinius-gone.img.zst.part").write_bytes(b"half")
(folder / "notes.txt").write_text("mine")
(tmp / "elsewhere.img").write_bytes(b"chosen")
(folder / "linked.img").symlink_to(tmp / "elsewhere.img")
path = app.download_image(app.Job(Quiet()), info, str(folder))
check("after a download two images are kept: the new one and the newest before it",
      sorted(os.listdir(folder)) == ["linked.img", "notes.txt", "plinius-1.img.zst", "plinius-old3.img.zst"])
check("what the app did not download stays", (tmp / "elsewhere.img").read_bytes() == b"chosen")
(folder / "plinius-old0.img.zst").write_bytes(b"old")
os.utime(folder / "plinius-old0.img.zst", (900_000, 900_000))
app.download_image(app.Job(Quiet()), info, str(folder))
check("also when the image was there already", not (folder / "plinius-old0.img.zst").exists()
      and (folder / "plinius-old3.img.zst").exists() and os.path.exists(path))
app.prune_images(str(folder / "plinius-1.img.zst"), str(tmp / "no-such-folder"))
check("a missing folder is no error", True)
app.urllib.request.urlopen = real_urlopen

# The drivers of Volla OS: vendor and vendor_dlkm from super of the phone into the written system. super here is a
# file with metadata as liblp writes it; the copy script runs in a shell on the PC, only mount is left out.
import struct  # noqa: E402


def lp_metadata(partitions):
    """One copy of liblp metadata (header 256 bytes, then the tables) for [(name, [(sectors, kind, target)])]."""
    ptable, extents = b"", []
    for name, pieces in partitions:
        ptable += struct.pack("<36sIIII", name.encode(), 0, len(extents), len(pieces), 0)
        extents += pieces
    etable = b"".join(struct.pack("<QIQI", sectors, kind, target, 0) for sectors, kind, target in extents)
    groups = struct.pack("<36sIQ", b"default", 0, 0)
    devices = struct.pack("<QIIQ36sI", 2048, 4096, 0, 64 << 20, b"super", 0)
    tables = ptable + etable + groups + devices
    descriptors = struct.pack("<12I", 0, len(partitions), 52, len(ptable), len(extents), 24,
                              len(ptable) + len(etable), 1, 48, len(ptable) + len(etable) + 48, 1, len(devices))
    def header(checksum):
        return (struct.pack("<IHHI32sI32s", app.LP_HEADER_MAGIC, 10, 2, 256, checksum, len(tables),
                            hashlib.sha256(tables).digest()) + descriptors + bytes(128))
    return header(hashlib.sha256(header(bytes(32))).digest()) + tables


def lp_super(path, slots, data, max_size=65536):
    """A super file: geometry twice, one metadata copy per slot ([partitions] per slot), data from 1 MiB on."""
    def geometry(checksum):
        return struct.pack("<II32sIII", app.LP_GEOMETRY_MAGIC, 52, checksum, max_size, len(slots), 4096)
    geo = geometry(hashlib.sha256(geometry(bytes(32))).digest()).ljust(4096, b"\0")
    meta = b"".join(lp_metadata(parts).ljust(max_size, b"\0") for parts in slots)
    head = (bytes(4096) + geo + geo + meta).ljust(1 << 20, b"\0")
    path.write_bytes(head + data)


super_file, system_dir = tmp / "super.img", tmp / "written-system"
body = os.urandom(3 << 20)
sector = lambda offset: (1 << 20) // 512 + offset // 512
# vendor_a: 512 KiB, 4 KiB of zeros, then 256 KiB that start off any 4 KiB border; vendor_dlkm_a: 1 MiB
vendor_a = [(1024, 0, sector(0)), (8, 1, 0), (512, 0, sector(1 << 20) + 3)]
dlkm_a = [(2048, 0, sector(2 << 20) - 2048)]
want_vendor = body[:512 << 10] + bytes(4096) + body[(1 << 20) + 1536:(1 << 20) + 1536 + (256 << 10)]
want_dlkm = body[1 << 20:2 << 20]
slot_a = [("system_a", [(8, 0, sector(0))]), ("vendor_a", vendor_a), ("vendor_dlkm_a", dlkm_a)]
# Each copy of the metadata names the partitions of both slots; this phone lacks vendor_dlkm_b
slot_b = [("vendor_b", [(16, 0, sector(0))]), ("vendor_dlkm_a", dlkm_a)]
lp_super(super_file, [slot_a, slot_b], body)
raw = super_file.read_bytes()
parts = app.lp_partitions(raw, 0)
check("the partitions of slot a with their pieces, zeros left out", parts["vendor_a"] == {
    "size": (1024 + 8 + 512) * 512,
    "pieces": [(0, 1 << 20, 512 << 10), ((1024 + 8) * 512, (1 << 20) + (1 << 20) + 1536, 256 << 10)]}
    and set(parts) == {"system_a", "vendor_a", "vendor_dlkm_a"})
check("slot b has its own metadata", set(app.lp_partitions(raw, 1)) == {"vendor_b", "vendor_dlkm_a"})
for what, damaged in (("geometry", 4096 + 50), ("header", 4096 * 3 + 60), ("tables", 4096 * 3 + 256 + 10)):
    bad = bytearray(raw)
    bad[damaged] ^= 1
    try:
        app.lp_partitions(bytes(bad), 0)
        check(f"damaged {what} refused", False)
    except ValueError:
        pass
check("damaged geometry, header or tables are refused", True)
check("dd's block: as large as every value allows", app.dd_block(1 << 20, 2 << 20, 3 << 20) == 1 << 20
      and app.dd_block(4096, 1536, 8192) == 512 and app.dd_block(512) == 512)


class Phone:
    """The phone for take_vendor: reads super from the file, runs scripts in a local shell into system_dir."""
    def __init__(self):
        self.scripts = []

    def reader(self, path, size):
        assert path == app.SUPER
        return subprocess.Popen(["head", "-c", str(size), str(super_file)], stdout=subprocess.PIPE)

    def run_root(self, script, timeout=60):
        self.scripts.append(script)
        if script == "plinius-partlinks":
            return 0, "", ""
        script = re.sub(r"m=\$\(mktemp[^\n]*", f"m={system_dir}", script)
        script = re.sub(r"\nmount -t ext4 [^\n]*", "", script)
        script = script.replace("""umount "$m"; rmdir "$m\"""", ":").replace(app.SUPER, str(super_file))
        r = subprocess.run(["sh"], input=script, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr


def take(pins, slot="a"):
    shutil.rmtree(system_dir, ignore_errors=True)
    system_dir.mkdir()
    phone = Phone()
    job = app.Job(Quiet())
    notes = app.take_vendor(job, types.SimpleNamespace(access=phone, info={"slot": slot}), "/dev/mmcblk0p2", pins)
    return notes, phone


android = system_dir / app.VENDOR_DIR
sha = lambda data: hashlib.sha256(data).hexdigest()
pins = {"vendor": (len(want_vendor), sha(want_vendor)), "vendor_dlkm": (len(want_dlkm), sha(want_dlkm))}
notes, phone = take(pins)
check("vendor and vendor_dlkm of slot a are copied piece by piece, and match", notes == []
      and (android / "vendor.img").read_bytes() == want_vendor and (android / "vendor_dlkm.img").read_bytes() == want_dlkm
      and not list(android.glob("*.new")))
check("the copy reads only super and writes only into the system", all(
    "if=/dev/disk/by-partlabel/super" in line and 'of="$d/' in line for line in phone.scripts[1].splitlines()
    if line.startswith("dd ")) and "mount -t ext4 /dev/mmcblk0p2" in phone.scripts[1])
notes, _phone = take(dict(pins, vendor=(len(want_vendor), "0" * 64)))
check("another vendor is taken all the same, with a note", len(notes) == 1 and notes[0].startswith("vendor of Volla OS")
      and (android / "vendor.img").read_bytes() == want_vendor)
notes, _phone = take({"vendor": pins["vendor"]}, slot="b")
check("slot b: its own vendor", notes and (android / "vendor.img").stat().st_size == 16 * 512)
notes, phone = take({"vendor_dlkm": pins["vendor_dlkm"]}, slot="b")
check("missing in its slot: the other slot's", notes == [] and (android / "vendor_dlkm.img").read_bytes() == want_dlkm)
lp_super(super_file, [slot_a + [("vendor_a-cow", [(8, 0, sector(0))])]], body)
notes, _phone = take(pins)
check("an update Volla OS has not merged, but the drivers are the tested ones: no note",
      notes == [] and (android / "vendor.img").read_bytes() == want_vendor)
notes, _phone = take(dict(pins, vendor=(len(want_vendor), "0" * 64)))
check("an update Volla OS has not merged and the drivers differ: one note that says so",
      len(notes) == 1 and "not finished an update" in notes[0] and (android / "vendor.img").read_bytes() == want_vendor)
lp_super(super_file, [[("system_a", [(8, 0, sector(0))])]], body)
notes, phone = take(pins)
check("not on the phone: a note for each, nothing copied, nothing stops",
      len(notes) == 2 and all("was not found" in n for n in notes) and not android.exists() and len(phone.scripts) == 1)
super_file.write_bytes(bytes(1 << 20))
notes, phone = take(pins)
check("super without metadata: one note, nothing copied", len(notes) == 1 and "could not be read" in notes[0]
      and not android.exists())
check("an image that brings its drivers wants nothing from the phone", app.take_vendor(None, None, "/x", {}) == [])
lp_super(super_file, [slot_a], body)
failing = Phone()
failing.run_root = lambda script, timeout=60: (0, "", "") if script == "plinius-partlinks" else (1, "", "No space left")
notes = app.take_vendor(app.Job(Quiet()), types.SimpleNamespace(access=failing, info={}), "/dev/mmcblk0p2", pins)
check("a copy that fails is a note, not a stop", len(notes) == 1 and "No space left" in notes[0])
try:
    app.vendor_script("/dev/sda1;reboot", False, {})
    check("a strange path is refused", False)
except app.Failed:
    check("a strange path or name is refused", True)

if shutil.which("mkfs.ext4"):
    for brings, want in ((False, {"vendor": (12, "a" * 64)}), (True, {})):
        tree = tmp / ("pins-tree-%d" % brings)
        (tree / app.VENDOR_DIR).mkdir(parents=True, exist_ok=True)
        (tree / app.VENDOR_PINS).parent.mkdir(parents=True, exist_ok=True)
        (tree / app.VENDOR_PINS).write_text(f"{'a' * 64} 12 vendor\nbroken line\n{'b' * 64} x vendor_dlkm\n")
        if brings:
            (tree / app.VENDOR_DIR / "vendor.img").write_bytes(b"x" * 12)
        img = tmp / ("pins-%d.img" % brings)
        subprocess.run(["mkfs.ext4", "-q", "-F", "-d", str(tree), str(img), "8M"], check=True)
        with open(img, "rb") as f:
            got = app.vendor_pins(app.Ext4Reader(f))
        check("the image names what it wants" if not brings else "an image with vendor.img wants nothing", got == want)

# The order of the boot entries as lwext4 counts them, against debugfs, which reads directories as stored.
# With many entries the directory gets a hash index, and Linux would list it in another order.
if shutil.which("mkfs.ext4") and shutil.which("debugfs"):
    tree = tmp / "settings"
    (tree / "db" / "entries").mkdir(parents=True)
    for i in range(300):
        (tree / "db" / "entries" / f"rom{i}.conf").write_text("title x\n")
    (tree / "db" / "entries" / "sub").mkdir()
    settings_img = tmp / "settings.img"
    subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "4096", "-d", str(tree), str(settings_img), "16M"], check=True)
    # e2fsck -D gives the directory its hash index, and stores the entries in hash order
    subprocess.run(["e2fsck", "-fyD", str(settings_img)], capture_output=True)

    class LocalShell:
        def run_root(self, script, timeout=60):
            script = script.replace("/dev/mmcblk0p1", str(settings_img))
            r = subprocess.run(["sh"], input=script, capture_output=True, text=True)
            return r.returncode, r.stdout, r.stderr

    listing = subprocess.run(["debugfs", "-R", "ls -p /db/entries", str(settings_img)], capture_output=True,
                             text=True, check=True).stdout
    stored = [f[5] for f in (line.split("/") for line in listing.splitlines()) if len(f) > 6 and f[2].startswith("100")]
    flags = re.search(r"Flags: (0x[0-9a-f]+)", subprocess.run(["debugfs", "-R", "stat /db/entries", str(settings_img)],
                                                              capture_output=True, text=True).stdout)
    # EXT4_INDEX_FL
    hashed = bool(flags) and bool(int(flags[1], 16) & 0x1000)
    order = app.entry_order(LocalShell(), "mmcblk0p1")
    check("the entries in the order the bootloader counts them, also with a hash index",
          order == stored and len(order) == 300 and hashed and order != sorted(order))

# Python before 3.14 has no compression.zstd: the app then works through python-zstandard
# (zstandard_compat). Checked against compression.zstd where it exists, else against python-zstandard.
try:
    import zstandard
except ImportError:
    zstandard = None
    print("  skipped: python-zstandard is not installed, so the zstd fallback for Python before 3.14 is untested")
if zstandard:
    z = app.zstandard_compat(zstandard)
    try:
        from compression import zstd as reference
    except ImportError:
        reference = None
    check("without compression.zstd the app runs on the fallback", reference is not None or isinstance(app.zstd, type(z)))

    def ref_compress(data, checksum=False):
        if reference:
            return reference.compress(data, options={reference.CompressionParameter.checksum_flag: checksum})
        return zstandard.ZstdCompressor(write_checksum=checksum).compress(data)

    def ref_stream(data):
        """Compressed without the content size in its header, as a stream is"""
        out = io.BytesIO()
        with zstandard.ZstdCompressor().stream_writer(out, closefd=False) as w:
            w.write(data)
        return out.getvalue()

    def ref_decompress(blob):
        if reference:
            return reference.decompress(blob)
        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(blob), read_across_frames=True).read()

    # Random data (raw blocks), zeros (RLE blocks) and text (compressed blocks)
    sample = os.urandom(300_000) + bytes(4 << 20) + b"text " * 60_000
    packed = tmp / "fallback.zst"
    level, workers = z.CompressionParameter.compression_level, z.CompressionParameter.nb_workers
    with z.open(packed, "wb", options={level: 3, workers: 4}) as w:
        for i in range(0, len(sample), 1 << 20):
            w.write(sample[i:i + (1 << 20)])
    check("what the fallback writes, with workers, unpacks elsewhere", ref_decompress(packed.read_bytes()) == sample)
    with z.open(str(packed), "rb") as f:
        pieces = list(iter(lambda: f.read(65536), b""))
    check("and the fallback reads it back piece by piece", b"".join(pieces) == sample and len(pieces) > 1)

    # Every kind of frame header: content size of 1, 2, 4 bytes or none, with and without checksum,
    # several frames in a row and a skippable frame between them
    skippable = (0x184D2A53).to_bytes(4, "little") + (3).to_bytes(4, "little") + b"abc"
    parts = [b"x" * 10, b"y" * 1000, os.urandom(70_000)]
    blob = ref_compress(parts[0]) + skippable + ref_compress(parts[1], checksum=True) + ref_stream(parts[2])
    check("several frames, skippable ones and every header", z.decompress(blob) == b"".join(parts))
    raw = io.BytesIO(blob)
    with z.open(raw, "rb") as f:
        whole = f.read()
    check("an open file stays open, it belongs to the caller", whole == b"".join(parts) and not raw.closed)

    def refused(blob, error):
        try:
            z.decompress(blob)
        except error:
            return True
        except Exception:  # noqa: BLE001
            return False
        return False

    full = ref_compress(sample, checksum=True)
    check("a cut-off stream is an error, wherever it was cut",
          all(refused(full[:cut], (EOFError, z.ZstdError)) for cut in (1, 4, 5, 9, 200, len(full) // 2, len(full) - 1)))
    check("an empty file is no stream", refused(b"", EOFError) and refused(skippable, EOFError))
    check("data after the last frame is an error", refused(full + b"garbage!", z.ZstdError))
    # Magic number, frame header without content size, window, then a block of the reserved type 3
    reserved = (0xFD2FB528).to_bytes(4, "little") + b"\x00\x00" + (3 << 1 | 1).to_bytes(3, "little")
    frames = app.ZstdFrames()
    frames.feed(reserved)
    check("a block of the reserved type is no stream", frames.bad and not frames.complete())
    try:
        z.open(packed, "ab")
        check("only reading and writing", False)
    except ValueError:
        check("only reading and writing", True)

# The image of the guided installation (ImageSource): fetched from the start, not over a metered line
class Listener:
    def __init__(self):
        self.changes = self.progress = 0

    def image_changed(self):
        self.changes += 1

    def image_progress(self):
        self.progress += 1


def sync(work, done):
    """in_thread without a thread: the work, then done() at once."""
    try:
        result, error = work(), None
    except Exception as e:  # noqa: BLE001
        result, error = None, str(e)
    done(result, error)


real = (app.in_thread, app.download_image, app.metered, app.SystemImage, app.alert)
app.in_thread = sync
downloads, checked = [], []
app.download_image = lambda job, info: downloads.append(info["name"]) or os.path.join(app.IMAGES, info["name"])


class FakeSystemImage:
    def __init__(self, path):
        self.path, self.size, self.problem, self.name, self.first_start = path, 5, "", "Arch Linux ARM", True

    def check(self):
        checked.append(self.path)

    def phone_problem(self):
        return ""

    def close(self):
        pass


app.SystemImage = FakeSystemImage
real_card_problem, app.card_image_problem = app.card_image_problem, lambda image: ""
offer = {"version": "2026.11.25", "name": "plinius-arch-25112026.img.zst", "size": 7, "sha256": "0" * 64,
         "url": "https://plinius.cais.de/images/plinius-arch-25112026.img.zst"}
app.metered = lambda: True
source, listener = app.ImageSource(None), Listener()
source.listeners["page"] = listener
source.show_offer(offer, None)
check("at the start the newest image is only named", not downloads and not source.looking
      and listener.changes == 1 and source.title() == "Plinius 2026.11.25")
source.want()
check("over a metered line the download waits for a click, also after deciding", not downloads)
app.metered = lambda: False
source = app.ImageSource(None)
source.listeners["page"] = listener
source.show_offer(offer, None)
check("nothing is downloaded before the user decides", not downloads and not source.image)
source.want()
check("deciding downloads the newest image, then checks it", downloads == [offer["name"]]
      and checked == [os.path.join(app.IMAGES, offer["name"])] and source.image and not source.job)
source.want()
check("a second decision downloads nothing again", downloads == [offer["name"]])
early = app.ImageSource(None)
early.want()
early.show_offer(offer, None)
check("deciding before the newest image is known downloads it once it is", downloads == [offer["name"]] * 2)
os.makedirs(app.IMAGES, exist_ok=True)
with open(os.path.join(app.IMAGES, offer["name"]), "wb") as f:
    f.write(b"x" * offer["size"])
here_already = app.ImageSource(None)
here_already.show_offer(offer, None)
check("an image downloaded before is checked without asking", downloads == [offer["name"]] * 3
      and here_already.image)
os.remove(os.path.join(app.IMAGES, offer["name"]))
check("the image from the server is named by its version", source.title() == "Plinius 2026.11.25")
source.download_progress(3, 7)
check("progress reaches the pages without drawing them anew", source.downloaded == (3, 7) and listener.progress == 1)
source.image.path = "/home/user/own.img"
check("an image of one's own by its file name", source.title() == "own.img")


# Pausing: what came so far stays as .part, and the next start goes on from there
paused = app.ImageSource(None)
paused.show_offer(offer, None)
check("nothing paused without a part", not paused.paused() and paused.part_size() == 0)
with open(os.path.join(app.IMAGES, offer["name"]) + ".part", "wb") as f:
    f.write(b"x" * 3)
check("a stopped download is paused, with how much is here", paused.paused() and paused.part_size() == 3)
stopped = []
paused.job = types.SimpleNamespace(cancel=lambda: stopped.append(True))
check("a running download is not paused", not paused.paused())
paused.pause()
check("pausing stops the running download", stopped == [True])
paused.job = None
paused.want()
check("deciding goes on with a paused download", downloads[-1] == offer["name"] and paused.image)
os.remove(os.path.join(app.IMAGES, offer["name"]) + ".part")


def failing(job, info):
    raise RuntimeError("connection lost")


app.download_image = failing

source = app.ImageSource(None)
source.offer = offer
source.start_download()
check("a download that fails says why and can start again", source.download_error == "connection lost"
      and not source.job and not source.image)
app.in_thread, app.download_image, app.metered, app.SystemImage, app.alert = real
app.card_image_problem = real_card_problem

# The details of an operation: each new step once, with the time
class Page:
    def __init__(self):
        self.lines, self.job = [], None

    def progress(self, done, total, text):
        return False

    def add_line(self, text):
        self.lines.append(text)
        return False


real_idle = app.GLib.idle_add
app.GLib.idle_add = lambda function, *args: function(*args)
page = Page()
job = app.Job(page)
for done, text in ((0, "Setting up the memory card"), (1, "Setting up the memory card"), (2, "Writing Plinius"),
                   (3, "Writing Plinius"), (4, "")):
    job.report(done, 4, text)
app.GLib.idle_add = real_idle
check("each new step goes into the details once, with the time",
      [line.split("  ", 1)[1] for line in page.lines] == ["Setting up the memory card", "Writing Plinius"]
      and all(re.fullmatch(r"\d\d:\d\d:\d\d", line.split("  ", 1)[0]) for line in page.lines))

print("All guide checks passed")
