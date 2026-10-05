import json
import os
import shutil
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

from ps2recomp.elf import ElfFile
from regionconfig.__main__ import HANDLERS, generate, run_check, run_translate, verify_inputs
from regionconfig.core import Translator, fmt_like
from regionmap import FuncMatch, Range, RegionMap, Uncertain
from regionmap.common import file_info
from regionmap.mapfile import merge_ranges

US = os.path.join(ROOT, "tmp", "us", "SLUS_208.51")
JP = os.path.join(ROOT, "tmp", "jp", "SLPS_254.18")
CONFIG = os.path.join(ROOT, "config")

SYMBOL_FILES = ("hooks.json", "overrides.json", "manual_symbols.json",
                "sdk_symbols.json", "game_symbols.txt")


def raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return True
    return False


def identity_map(us_elf_path, ida_db_path):
    """Map every address to itself: one delta-0 range per allocated section and
    every ida function matched as 'same' at the same address."""
    elf = ElfFile(us_elf_path)
    secs, ranges = [], []
    for s in elf.sections:
        if s.is_alloc and s.size > 0:
            secs.append({"name": s.name, "us_start": s.addr, "us_end": s.addr + s.size,
                         "jp_start": s.addr, "jp_end": s.addr + s.size,
                         "nobits": s.type == 8})
            ranges.append(Range(s.addr, s.addr + s.size, 0, s.name, "section", "high"))
    with open(ida_db_path) as fp:
        db = json.load(fp)
    funcs = []
    for f in db["functions"]:
        main = next((c for c in f["chunks"] if c[0] == f["ea"]), f["chunks"][0])
        chunks = [[c[0], c[1], c[0], "same"] for c in f["chunks"]]
        funcs.append(FuncMatch(f["ea"], main[1], f["name"], f["ea"], main[1], "same",
                               "exact", 1.0, 1.0, "", chunks))
    info = file_info(elf)
    return RegionMap(info, dict(info), secs, merge_ranges(ranges), [], funcs, {})


def synthetic_map():
    ranges = [Range(0x100, 0x200, 0x10, ".text", "func", "high"),
              Range(0x300, 0x400, 0x20, ".text", "func", "high")]
    unc = [Uncertain(0x200, 0x240, ".text", [], "content differs")]
    funcs = [
        FuncMatch(0x100, 0x140, "sub_100", 0x110, 0x150, "same", "exact", 1.0, 1.0),
        FuncMatch(0x140, 0x180, "sub_140", 0x150, 0x190, "same", "exact", 1.0, 1.0, "",
                  [[0x140, 0x180, 0x150, "same"], [0x1C0, 0x1E0, 0x1D0, "same"]]),
        # Entry word changed: the match places it at 0x1A8, the range at 0x190.
        FuncMatch(0x180, 0x1C0, "sub_180", 0x1A8, 0x1E8, "body-changed", "diff", 0.9, 0.9),
        FuncMatch(0x340, 0x380, "sub_340", None, None, "unmatched", "none", 0.0, 0.0),
    ]
    secs = [{"name": ".text", "us_start": 0x100, "us_end": 0x400,
             "jp_start": 0x110, "jp_end": 0x420, "nobits": False}]
    info = {"file": "US", "sha256": "aa"}
    return RegionMap(info, {"file": "JP", "sha256": "bb"}, secs, ranges, unc, funcs, {})


def test_lookups():
    tr = Translator(synthetic_map(), None, None)
    assert tr.func_entry(0x100) == 0x110, "main start"
    assert tr.func_entry(0x1C0) == 0x1D0, "chunk start"
    assert tr.func_entry(0x180) == 0x1A8, "body-changed entry differs from translate"
    assert tr.rmap.translate(0x180) == 0x190
    assert tr.func_entry(0x104) == 0x114, "falls back to translate"
    assert tr.func_entry(0x340) == 0x360, "unmatched function: range still maps it"
    assert tr.func_entry(0x210) is None and tr.func_entry(0x50) is None
    assert tr.addr(0x1C0) == 0x1D0

    assert tr.end(0x140) == 0x150
    assert tr.end(0x200) == 0x210, "exclusive end of a range maps through the last byte"
    assert tr.end(0x201) is None

    assert tr.why(0x210) == "uncertain: content differs"
    assert tr.why(0x50) == "outside every mapped range"
    assert tr.rmap.function_at(0x340).name == "sub_340"
    tr2 = Translator(synthetic_map(), None, None)
    tr2.rmap.ranges[:] = [r for r in tr2.rmap.ranges if r.us_start != 0x300]
    tr2.rmap._index()
    assert tr2.why(0x340) == "unmatched function sub_340"
    assert tr.why(0x190) == "changed code in body-changed sub_180"

    assert tr.jp_spans(0x100) == [(0x110, 0x150)]
    assert tr.jp_spans(0x140) == [(0x150, 0x190), (0x1D0, 0x1F0)]
    assert tr.jp_spans(0x180) == [(0x1A8, 0x1E8)]
    assert tr.jp_spans(0x50) == []


def test_rename():
    tr = Translator(synthetic_map(), None, None)
    assert tr.rename("sub_0028EAE8", 0x28EAE8, 0x28EE28) == "sub_0028EE28"
    assert tr.rename("sub_28EAE8", 0x28EAE8, 0x28EE28) == "sub_28EE28", "width is kept"
    assert tr.rename("loc_0028eae8", 0x28EAE8, 0x28EE28) == "loc_0028ee28", "case is kept"
    assert tr.rename("sub_0028EAE8", 0x28EAE0, 0x28EE28) == "sub_0028EAE8", "hex must match us"
    assert tr.rename("sceSifInitRpc", 0x28EAE8, 0x28EE28) == "sceSifInitRpc"
    assert tr.rename("_sceCd_cd_read_intr", 0x28EAE8, 0x28EE28) == "_sceCd_cd_read_intr"
    assert fmt_like("0x0033e920", 0xABC) == "0x00000abc"
    assert fmt_like("0x0033E9F0", 0xABC) == "0x00000ABC"
    assert fmt_like("00101B70", 0x101C70) == "00101C70"


def write(path, text):
    with open(path, "w", newline="\n") as fp:
        fp.write(text)


def make_src(d, hooks, overrides, sdk):
    write(os.path.join(d, "hooks.json"), json.dumps(hooks))
    write(os.path.join(d, "overrides.json"), json.dumps(overrides))
    write(os.path.join(d, "manual_symbols.json"),
          json.dumps({"0x00000100": {"name": "m", "src": "x"}}))
    write(os.path.join(d, "sdk_symbols.json"), json.dumps(sdk, indent=1) + "\n")
    write(os.path.join(d, "game_symbols.txt"),
          "# old header\n# ADDRESS NAME\n00000104 a  # c1\n00000500 gone\n00000140 b\n")


def test_fail_and_drop():
    root = tempfile.mkdtemp()
    try:
        src = os.path.join(root, "src")
        os.makedirs(src)
        sdk = {"0x00000100": {"name": "keep", "lib": "l", "words": 1},
               "0x00000500": {"name": "gone", "lib": "l", "words": 1}}
        good_overrides = {"hle_a": "x", "0x00000140": "native_b"}

        # An unmapped hook fails the run and writes nothing.
        make_src(src, {"0x00000500": "hook_x"}, good_overrides, sdk)
        out = os.path.join(root, "out")
        log = []
        tr = Translator(synthetic_map(), None, None)
        assert run_translate(tr, HANDLERS, src, out, log.append) == 1
        assert not os.path.exists(out)
        assert any(l.startswith("FAIL hooks.json hooks:0x00000500 us=0x00000500: "
                                "outside every mapped range") for l in log), log

        # An unmapped override address fails too.
        make_src(src, {"0x00000100": "hook_x"}, {"0x00000500": "n"}, sdk)
        tr = Translator(synthetic_map(), None, None)
        assert run_translate(tr, HANDLERS, src, out, [].append) == 1

        # An unmapped sdk symbol is dropped and listed.
        make_src(src, {"0x00000100": "hook_x"}, good_overrides, sdk)
        tr = Translator(synthetic_map(), None, None)
        log = []
        assert run_translate(tr, HANDLERS, src, out, log.append) == 0, log
        assert json.load(open(os.path.join(out, "sdk_symbols.json"))) == \
            {"0x00000110": {"name": "keep", "lib": "l", "words": 1}}
        assert json.load(open(os.path.join(out, "hooks.json"))) == {"0x00000110": "hook_x"}
        assert json.load(open(os.path.join(out, "overrides.json"))) == \
            {"hle_a": "x", "0x00000150": "native_b"}
        man = json.load(open(os.path.join(out, "manifest.json")))
        assert man["files"]["sdk_symbols.json"] == {"kept": 1, "dropped": 1, "rederived": 0}
        sdk_drop = [d for d in man["dropped"] if d["file"] == "sdk_symbols.json"]
        assert [(d["where"], d["reason"]) for d in sdk_drop] == \
            [("sdk_symbols:0x00000500", "outside every mapped range")]
        assert any(d["file"] == "game_symbols.txt" and d["us"] == "0x00000500"
                   for d in man["dropped"])
        with open(os.path.join(out, "game_symbols.txt")) as fp:
            lines = fp.read().split("\n")
        assert lines[0].startswith("# Names for SLPS_254.18's functions, translated from "
                                   "config/game_symbols.txt")
        assert lines[1:5] == ["# ADDRESS NAME", "00000114 a  # c1", "00000150 b", ""], lines
        for name in os.listdir(out):
            with open(os.path.join(out, name), "rb") as fp:
                assert b"\r" not in fp.read()

        # A body-changed target is noted; an unmapped sdk symbol that an
        # override resolves by name fails.
        make_src(src, {"0x00000180": "hook_bc"}, good_overrides, sdk)
        tr = Translator(synthetic_map(), None, None)
        assert run_translate(tr, HANDLERS, src, out, [].append) == 0
        assert json.load(open(os.path.join(out, "hooks.json"))) == {"0x000001A8": "hook_bc"}
        assert [n["note"] for n in json.load(open(os.path.join(out, "manifest.json")))["notes"]
                if n["file"] == "hooks.json"] == ["target is in body-changed sub_180"]

        make_src(src, {"0x00000100": "hook_x"}, {"gone": "n"}, sdk)
        tr = Translator(synthetic_map(), None, None)
        log = []
        assert run_translate(tr, HANDLERS, src, out + "2", log.append) == 1
        assert any("FAIL sdk_symbols.json sdk_symbols:0x00000500" in l for l in log), log
        assert not os.path.exists(out + "2")

        # check: unchanged -> 0, modified or missing file -> 1.
        make_src(src, {"0x00000100": "hook_x"}, good_overrides, sdk)
        assert run_translate(Translator(synthetic_map(), None, None), HANDLERS, src,
                             out, [].append) == 0
        log = []
        assert run_check(Translator(synthetic_map(), None, None), HANDLERS, src, out,
                         log.append) == 0
        assert log[-1].endswith("is up to date"), log
        with open(os.path.join(out, "hooks.json"), "a") as fp:
            fp.write(" ")
        os.remove(os.path.join(out, "manifest.json"))
        log = []
        assert run_check(Translator(synthetic_map(), None, None), HANDLERS, src, out,
                         log.append) == 1
        assert "regionconfig: hooks.json differs" in log
        assert "regionconfig: manifest.json differs" in log
        assert "regionconfig: overrides.json differs" not in log
        # Regenerating restores a clean state; extra files in --out (report.json)
        # do not matter.
        assert run_translate(Translator(synthetic_map(), None, None), HANDLERS, src,
                             out, [].append) == 0
        write(os.path.join(out, "report.json"), "{}")
        log = []
        assert run_check(Translator(synthetic_map(), None, None), HANDLERS, src, out,
                         log.append) == 0, log
    finally:
        shutil.rmtree(root)


def test_duplicates_and_order():
    root = tempfile.mkdtemp()
    try:
        src = os.path.join(root, "src")
        os.makedirs(src)
        sdk = {"0x00000104": {"name": "b"}, "0x00000100": {"name": "a"}}
        make_src(src, {}, {}, sdk)
        out = generate(Translator(synthetic_map(), None, None), HANDLERS, src, [].append)
        assert out is not None
        assert list(json.loads(out["sdk_symbols.json"])) == ["0x00000114", "0x00000110"], \
            "US key order is kept"
        # 0x180 is placed at 0x1A8 by its match and 0x198 maps there by range.
        make_src(src, {"0x00000180": "h1", "0x00000198": "h2"}, {}, sdk)
        log = []
        assert generate(Translator(synthetic_map(), None, None), HANDLERS, src,
                        log.append) is None
        assert any("hooks.json hooks:0x00000198" in l and "maps onto 0x000001A8" in l
                   for l in log), log
    finally:
        shutil.rmtree(root)


def test_verify_inputs():
    class Fake:
        def __init__(self, data):
            self.data, self.path = data, "x"
    rmap = synthetic_map()
    assert raises(SystemExit, verify_inputs, rmap, Fake(b"a"), Fake(b"b"))


def test_real_identity():
    us = ElfFile(US)
    rmap = identity_map(US, os.path.join(CONFIG, "ida_db.json"))
    rmap.validate()
    tr = Translator(rmap, us, us)
    outputs = generate(tr, HANDLERS, CONFIG, print)
    assert outputs is not None
    assert not tr.dropped and not tr.rederived, tr.dropped[:3]
    for name in SYMBOL_FILES:
        with open(os.path.join(CONFIG, name), "rb") as fp:
            raw = fp.read()
        if name.endswith(".json"):
            assert json.loads(outputs[name]) == json.loads(raw), name
            # Same key order, not just the same mapping.
            assert list(json.loads(outputs[name])) == list(json.loads(raw)), name
        else:
            a = raw.decode("utf-8").split("\n")
            b = outputs[name].decode("utf-8").split("\n")
            assert len(a) == len(b) and a[1:] == b[1:], name
            assert b[0].startswith("# Names for SLPS_254.18's functions")
        assert outputs[name].endswith(b"\n") == raw.endswith(b"\n"), name
    # Every hook and override address key is resolved by the identity map.
    assert not tr.failures


def test_real_pair():
    from regionconfig.__main__ import main
    out = tempfile.mkdtemp()
    try:
        mp = os.path.join(ROOT, "tmp", "regionmap", "regionmap.json")
        if not os.path.isfile(mp):
            print("SKIP: tmp/regionmap/regionmap.json missing")
            return
        argv = ["translate", "--out", out]
        assert main(argv) == 0
        assert main(["check", "--out", out]) == 0
        man = json.load(open(os.path.join(out, "manifest.json")))
        assert man["files"]["hooks.json"] == {"kept": 9, "dropped": 0, "rederived": 0}
        assert man["files"]["manual_symbols.json"] == {"kept": 10, "dropped": 0, "rederived": 0}
        ov = json.load(open(os.path.join(out, "overrides.json")))
        assert all(k in ov for k in ("0x0033EC60", "0x0033E708", "0x0033F138"))
        assert all(d["reason"] for d in man["dropped"])
        with open(os.path.join(out, "hooks.json")) as fp:
            assert json.load(fp)["0x0034BCD8"] == "hook_sound_load"
    finally:
        shutil.rmtree(out)


test_lookups()
test_rename()
test_fail_and_drop()
test_duplicates_and_order()
test_verify_inputs()

if os.path.isfile(US):
    test_real_identity()
if os.path.isfile(US) and os.path.isfile(JP):
    test_real_pair()
    print("PASS: regionconfig (synthetic + real binaries)")
elif os.path.isfile(US):
    print("SKIP: JP binary not present")
    print("PASS: regionconfig (synthetic + US identity)")
else:
    print("SKIP: real binaries not present")
    print("PASS: regionconfig (synthetic)")
