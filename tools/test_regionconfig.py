import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

from ps2recomp.elf import ElfFile
from regionconfig import idadb
from regionconfig.__main__ import HANDLERS, generate, run_check, run_translate, verify_inputs
from regionconfig.core import Translator, fmt_like
from regionmap import FuncMatch, Range, RegionMap, Uncertain
from regionmap.common import file_info
from regionmap.mapfile import merge_ranges

US = os.path.join(ROOT, "tmp", "us", "SLUS_208.51")
JP = os.path.join(ROOT, "tmp", "jp", "SLPS_254.18")
CONFIG = os.path.join(ROOT, "config")

# The synthetic tests have no ida_db.json source or ELF, so they skip that handler.
SYMBOL_HANDLERS = [h for h in HANDLERS if h is not idadb.handle]

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
        assert run_translate(tr, SYMBOL_HANDLERS, src, out, log.append) == 1
        assert not os.path.exists(out)
        assert any(l.startswith("FAIL hooks.json hooks:0x00000500 us=0x00000500: "
                                "outside every mapped range") for l in log), log

        # An unmapped override address fails too.
        make_src(src, {"0x00000100": "hook_x"}, {"0x00000500": "n"}, sdk)
        tr = Translator(synthetic_map(), None, None)
        assert run_translate(tr, SYMBOL_HANDLERS, src, out, [].append) == 1

        # An unmapped sdk symbol is dropped and listed.
        make_src(src, {"0x00000100": "hook_x"}, good_overrides, sdk)
        tr = Translator(synthetic_map(), None, None)
        log = []
        assert run_translate(tr, SYMBOL_HANDLERS, src, out, log.append) == 0, log
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
        assert run_translate(tr, SYMBOL_HANDLERS, src, out, [].append) == 0
        assert json.load(open(os.path.join(out, "hooks.json"))) == {"0x000001A8": "hook_bc"}
        assert [n["note"] for n in json.load(open(os.path.join(out, "manifest.json")))["notes"]
                if n["file"] == "hooks.json"] == ["target is in body-changed sub_180"]

        make_src(src, {"0x00000100": "hook_x"}, {"gone": "n"}, sdk)
        tr = Translator(synthetic_map(), None, None)
        log = []
        assert run_translate(tr, SYMBOL_HANDLERS, src, out + "2", log.append) == 1
        assert any("FAIL sdk_symbols.json sdk_symbols:0x00000500" in l for l in log), log
        assert not os.path.exists(out + "2")

        # check: unchanged -> 0, modified or missing file -> 1.
        make_src(src, {"0x00000100": "hook_x"}, good_overrides, sdk)
        assert run_translate(Translator(synthetic_map(), None, None), SYMBOL_HANDLERS, src,
                             out, [].append) == 0
        log = []
        assert run_check(Translator(synthetic_map(), None, None), SYMBOL_HANDLERS, src, out,
                         log.append) == 0
        assert log[-1].endswith("is up to date"), log
        with open(os.path.join(out, "hooks.json"), "a") as fp:
            fp.write(" ")
        os.remove(os.path.join(out, "manifest.json"))
        log = []
        assert run_check(Translator(synthetic_map(), None, None), SYMBOL_HANDLERS, src, out,
                         log.append) == 1
        assert "regionconfig: hooks.json differs" in log
        assert "regionconfig: manifest.json differs" in log
        assert "regionconfig: overrides.json differs" not in log
        # Regenerating restores a clean state; extra files in --out (report.json)
        # do not matter.
        assert run_translate(Translator(synthetic_map(), None, None), SYMBOL_HANDLERS, src,
                             out, [].append) == 0
        write(os.path.join(out, "report.json"), "{}")
        log = []
        assert run_check(Translator(synthetic_map(), None, None), SYMBOL_HANDLERS, src, out,
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
        out = generate(Translator(synthetic_map(), None, None), SYMBOL_HANDLERS, src, [].append)
        assert out is not None
        assert list(json.loads(out["sdk_symbols.json"])) == ["0x00000114", "0x00000110"], \
            "US key order is kept"
        # 0x180 is placed at 0x1A8 by its match and 0x198 maps there by range.
        make_src(src, {"0x00000180": "h1", "0x00000198": "h2"}, {}, sdk)
        log = []
        assert generate(Translator(synthetic_map(), None, None), SYMBOL_HANDLERS, src,
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
    outputs = generate(tr, SYMBOL_HANDLERS, CONFIG, print)
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


def fake_elf(entry, sections):
    secs = [SimpleNamespace(name=n, addr=a, size=z, is_alloc=True) for n, a, z in sections]
    return SimpleNamespace(entry=entry, sections=secs, path="x")


def ida_segments():
    """A LOAD gap, an exclusive-end section and a segment that IDA upper-cases."""
    return [{"name": ".text", "start": 0x100, "end": 0x400, "perm": 5, "type": 2, "bitness": 1},
            {"name": "REGINFO", "start": 0x400, "end": 0x418, "perm": 4, "type": 3, "bitness": 1},
            {"name": "LOAD", "start": 0x418, "end": 0x420, "perm": 6, "type": 3, "bitness": 1},
            {"name": ".data", "start": 0x420, "end": 0x500, "perm": 6, "type": 3, "bitness": 1}]


def ida_us_elf():
    return fake_elf(0x120, [(".text", 0x100, 0x300), (".reginfo", 0x400, 0x18),
                            (".data", 0x420, 0xE0)])


def ida_db_dict(funcs, names):
    return {"meta": {"imagebase": 0x100, "entry": 0x120, "min_ea": 0x100, "max_ea": 0x500,
                     "input": "US"},
            "segments": ida_segments(), "functions": funcs, "switches": [], "names": names}


def ifunc(ea, name, chunks):
    return {"ea": ea, "name": name, "chunks": chunks, "flags": 0, "noret": False,
            "thunk": False, "lib": False}


def test_ida_segments():
    us = ida_us_elf()
    root = tempfile.mkdtemp()
    try:
        write(os.path.join(root, "ida_db.json"), json.dumps(ida_db_dict([], {})))

        def run(jp, us_elf=us):
            tr = Translator(synthetic_map(), us_elf, jp)
            return tr, json.loads(idadb.handle(tr, root, switches=False)[idadb.FILE])

        # JP section headers give the exclusive ends; the gap spans between them.
        jp = fake_elf(0x130, [(".text", 0x110, 0x310), (".reginfo", 0x420, 0x18),
                              (".data", 0x440, 0xE0)])
        tr, out = run(jp)
        assert not tr.failures, tr.failures
        assert [(g["name"], g["start"], g["end"]) for g in out["segments"]] == \
            [(".text", 0x110, 0x420), ("REGINFO", 0x420, 0x438),
             ("LOAD", 0x438, 0x440), (".data", 0x440, 0x520)]
        assert out["segments"][2]["perm"] == 6, "other fields are copied"
        assert out["meta"]["entry"] == 0x130 and out["meta"]["input"] == "SLPS_254.18"
        assert (out["meta"]["min_ea"], out["meta"]["max_ea"]) == (0x110, 0x520)
        assert out["meta"]["imagebase"] == 0x100

        # A gap that closes in the JP image is dropped and listed.
        jp2 = fake_elf(0x130, [(".text", 0x110, 0x310), (".reginfo", 0x420, 0x18),
                               (".data", 0x438, 0xE0)])
        tr, out = run(jp2)
        assert not tr.failures
        assert [g["name"] for g in out["segments"]] == [".text", "REGINFO", ".data"]
        assert [(e.where, e.us, e.text) for e in tr.dropped] == \
            [("segments[LOAD@0x00000418]", 0x418, "empty gap between JP sections")]

        # A section that the JP image lacks fails.
        jp3 = fake_elf(0x130, [(".text", 0x110, 0x310), (".reginfo", 0x420, 0x18)])
        tr, out = run(jp3)
        assert [e.where for e in tr.failures] == ["segments[.data@0x00000420]"]

        # A segment that is not a US section fails too.
        bad = fake_elf(0x120, [(".text", 0x100, 0x2F0), (".reginfo", 0x400, 0x18),
                               (".data", 0x420, 0xE0)])
        tr, out = run(jp, bad)
        assert any(e.where == "segments[.text@0x00000100]" for e in tr.failures)
    finally:
        shutil.rmtree(root)


def test_ida_functions_and_names():
    us = ida_us_elf()
    jp = fake_elf(0x130, [(".text", 0x110, 0x310), (".reginfo", 0x420, 0x18),
                          (".data", 0x440, 0xE0)])
    rmap = synthetic_map()
    rmap.functions.append(
        FuncMatch(0x380, 0x3A0, "sub_380", 0x3A0, 0x3C0, "body-changed", "diff", 0.9, 0.9, "",
                  [[0x380, 0x3A0, 0x3A0, "same"], [0x3C0, 0x3D0, 0x3E0, "diff"]]))
    rmap._index()
    funcs = [
        ifunc(0x100, "sub_000100", [[0x100, 0x140], [0x210, 0x220]]),
        ifunc(0x140, "sub_140", [[0x140, 0x180], [0x1C0, 0x1E0]]),
        ifunc(0x180, "sub_000180", [[0x180, 0x1C0]]),
        ifunc(0x340, "sub_340", [[0x340, 0x380]]),
        ifunc(0x380, "sub_380", [[0x380, 0x3A0], [0x3C0, 0x3D0]]),
    ]
    names = {"256": "sub_000100", "260": "loc_000104", "272": "keep", "1280": "gone",
             "528": "ignored"}
    root = tempfile.mkdtemp()
    try:
        write(os.path.join(root, "ida_db.json"), json.dumps(ida_db_dict(funcs, names)))
        tr = Translator(rmap, us, jp)
        out = json.loads(idadb.handle(tr, root, switches=False)[idadb.FILE])
        assert not tr.failures, tr.failures
        got = {f["ea"]: f for f in out["functions"]}
        assert list(got) == [0x110, 0x150, 0x1A8, 0x3A0], "unmatched dropped, order kept"
        assert got[0x110]["name"] == "sub_000110"
        assert got[0x110]["chunks"] == [[0x110, 0x150]], "unmapped chunk dropped"
        assert got[0x150]["chunks"] == [[0x150, 0x190], [0x1D0, 0x1F0]], "same chunk keeps length"
        assert got[0x1A8]["chunks"] == [[0x1A8, 0x1E8]], "body-changed uses the match span"
        assert got[0x1A8]["name"] == "sub_0001A8"
        assert got[0x3A0]["chunks"] == [[0x3A0, 0x3C0], [0x3E0, 0x3F0]], "other chunk: end()"
        assert got[0x3A0]["name"] == "sub_380", "names that are not dummies stay"
        assert got[0x150]["flags"] == 0 and got[0x150]["thunk"] is False
        assert {e.where for e in tr.dropped} == {
            "functions[ea=0x00000100]:chunk 0x00000210", "functions[ea=0x00000340]",
            "names[1280]", "names[528]"}, tr.dropped
        assert any(e.where == "functions[ea=0x00000180]" and e.jp == 0x1A8 for e in tr.notes)
        assert out["names"] == {"272": "sub_000110", "276": "loc_000114", "288": "keep"}
        assert list(out) == ["meta", "segments", "functions", "switches", "names"]

        # Two functions on one JP address fail.
        rmap2 = synthetic_map()
        rmap2.functions[1].jp = 0x110
        write(os.path.join(root, "ida_db.json"), json.dumps(ida_db_dict(funcs[:2], {})))
        tr = Translator(rmap2, us, jp)
        idadb.handle(tr, root, switches=False)
        assert [e.where for e in tr.failures] == ["functions[ea=0x00000140]"]
    finally:
        shutil.rmtree(root)


JR_V0 = 0x00400008
JR_RA = 0x03E00008


def sltiu(imm):
    return (0x0B << 26) | (2 << 21) | (2 << 16) | imm


def switch_fixture(us_extra=None, jp_extra=None, **over):
    """A switch in sub_140 (US 0x140.., chunk 0x1C0..) that maps by delta
    0x10 / 0x20. US: bound 3 at 0x164, jr at 0x16C, table at 0x330. JP: jr at
    0x17C, table at 0x350."""
    s = {"ea": 0x16C, "func": 0x140, "jumps": 0x330, "ncases": 3, "elbase": 0,
         "startea": 0x160, "targets": [0x144, 0x148, 0x1C4]}
    s.update(over)
    us = {0x164: sltiu(3), 0x16C: JR_V0}
    jp = {0x174: sltiu(3), 0x17C: JR_V0}
    for i, t in enumerate([0x144, 0x148, 0x1C4]):
        us[0x330 + 4 * i] = t
    for i, t in enumerate([0x154, 0x158, 0x1D4]):
        jp[0x350 + 4 * i] = t
    us.update(us_extra or {})
    jp.update(jp_extra or {})
    return s, us, jp


def run_switch(s, us, jp, rmap=None):
    tr = Translator(rmap or synthetic_map(), None, None)
    return idadb.translate_switch(tr, s, lambda a: us.get(a, 0), lambda a: jp.get(a, 0),
                                  lambda a: 0x110 <= a < 0x420)


def switch_fails(s, us, jp, text):
    try:
        run_switch(s, us, jp)
    except idadb.SwitchError as e:
        assert text in str(e), str(e)
        return
    raise AssertionError("expected a SwitchError containing %r" % text)


def test_switch_logic():
    # The table equals the translated US targets: the US list carries over.
    s, us, jp = switch_fixture()
    assert idadb.self_check(lambda a: us.get(a, 0), s) == (True, True)
    new, re, notes = run_switch(s, us, jp)
    assert not re and not notes
    assert (new["ea"], new["func"], new["jumps"], new["startea"], new["ncases"]) == \
        (0x17C, 0x150, 0x350, 0x170, 3)
    assert new["targets"] == [0x154, 0x158, 0x1D4]
    assert s["targets"] == [0x144, 0x148, 0x1C4], "the input is not modified"

    # Duplicates and order of the US list are kept when the sets agree.
    s2, us2, jp2 = switch_fixture(targets=[0x148, 0x144, 0x1C4], ncases=4,
                                  us_extra={0x164: sltiu(4), 0x33C: 0x148},
                                  jp_extra={0x174: sltiu(4), 0x35C: 0x158})
    new, re, _ = run_switch(s2, us2, jp2)
    assert not re and new["targets"] == [0x158, 0x154, 0x1D4] and new["ncases"] == 4

    # One extra case in the JP table: the bound grows and the entries win.
    s, us, jp = switch_fixture(jp_extra={0x174: sltiu(4), 0x35C: 0x15C})
    new, re, _ = run_switch(s, us, jp)
    assert re and new["ncases"] == 4 and new["targets"] == [0x154, 0x158, 0x1D4, 0x15C]

    # Without a matching US bound the US case count is used, not the JP bound.
    s, us, jp = switch_fixture(us_extra={0x164: sltiu(9)}, jp_extra={0x174: sltiu(4),
                                                                     0x35C: 0x15C})
    assert idadb.self_check(lambda a: us.get(a, 0), s) == (True, False)
    new, re, _ = run_switch(s, us, jp)
    assert not re and new["ncases"] == 3

    # An entry outside the function (or outside .text, or unaligned) fails.
    switch_fails(*switch_fixture(jp_extra={0x358: 0x1A0}), "outside the function")
    switch_fails(*switch_fixture(jp_extra={0x358: 0x9000}), "not in .text")
    switch_fails(*switch_fixture(jp_extra={0x358: 0x1D5}), "not in .text")
    # A mapped US target that the JP table lost fails.
    switch_fails(*switch_fixture(jp_extra={0x358: 0x154}), "missing from the JP table")
    # The span test is off when a US target leaves the function: only .text applies.
    s, us, jp = switch_fixture(targets=[0x144, 0x148, 0x210],
                               us_extra={0x338: 0x210}, jp_extra={0x358: 0x300})
    new, re, _ = run_switch(s, us, jp)
    assert re and new["targets"] == [0x154, 0x158, 0x300], "unmapped US target: rederived"
    # ... and the JP entries stay checked against .text.
    switch_fails(s, us, switch_fixture(jp_extra={0x358: 0x500})[2], "not in .text")

    # No SLTIU before the JP jr although the US bound held.
    switch_fails(*switch_fixture(jp_extra={0x174: 0}), "no SLTIU bound")
    # The JP address is not a jr, or is a return.
    switch_fails(*switch_fixture(jp_extra={0x17C: 0}), "not a jr")
    switch_fails(*switch_fixture(jp_extra={0x17C: JR_RA}), "not a jr")
    # Unmapped ea / table address (the fallbacks are not implemented).
    switch_fails(*switch_fixture(ea=0x210), "step 2")
    switch_fails(*switch_fixture(jumps=0x210), "table address is unmapped")
    switch_fails(*switch_fixture(func=0x50), "step 6")

    # A table that disagrees with the US targets: plain translation.
    s, us, jp = switch_fixture(us_extra={0x338: 0x14C})
    assert idadb.self_check(lambda a: us.get(a, 0), s) == (False, True)
    new, re, _ = run_switch(s, us, jp)
    assert not re and new["targets"] == [0x154, 0x158, 0x1D4] and new["ncases"] == 3
    s, us, jp = switch_fixture(us_extra={0x338: 0x14C}, targets=[0x144, 0x148, 0x210])
    switch_fails(s, us, jp, "step 4")

    # startea outside the map: the new jr stands in for it, with a note.
    s, us, jp = switch_fixture(startea=0x210)
    new, re, notes = run_switch(s, us, jp)
    assert new["startea"] == 0x17C and len(notes) == 1

    # elbase is translated; 0 stays 0.
    s, us, jp = switch_fixture(elbase=0x100, us_extra={0x330: 0x44, 0x334: 0x48, 0x338: 0xC4},
                               jp_extra={0x350: 0x44, 0x354: 0x48, 0x358: 0xC4})
    new, re, _ = run_switch(s, us, jp)
    assert not re and new["elbase"] == 0x110 and new["targets"] == [0x154, 0x158, 0x1D4]
    assert idadb.read_table(lambda a: 0xFFFFFFF0, 0, 1, 0x20) == [0x10], "wraps at 32 bits"


def test_real_ida_identity():
    us = ElfFile(US)
    rmap = identity_map(US, os.path.join(CONFIG, "ida_db.json"))
    tr = Translator(rmap, us, us)
    raw = idadb.handle(tr, CONFIG, switches=False)[idadb.FILE]
    assert not tr.failures and not tr.dropped and not tr.rederived, tr.failures[:3]
    with open(os.path.join(CONFIG, "ida_db.json"), "rb") as fp:
        src = fp.read()
    a, b = json.loads(src), json.loads(raw)
    assert list(a) == list(b)
    for k in ("imagebase", "entry", "min_ea", "max_ea"):
        assert a["meta"][k] == b["meta"][k], k
    assert b["meta"]["input"] == "SLPS_254.18"
    assert a["segments"] == b["segments"]
    assert a["functions"] == b["functions"]
    assert a["names"] == b["names"] and list(a["names"]) == list(b["names"])
    assert b["switches"] == []
    assert raw.endswith(b"\n") == src.endswith(b"\n")
    assert b"\n" not in raw.rstrip(b"\n"), "one line, like the US file"

    # With switches, the identity map reproduces every record unchanged.
    tr = Translator(rmap, us, us)
    full = json.loads(idadb.handle(tr, CONFIG)[idadb.FILE])
    assert not tr.failures and not tr.rederived, (tr.failures[:3], tr.rederived[:3])
    assert full["switches"] == a["switches"]


def test_real_pair():
    from regionconfig.__main__ import main
    root = tempfile.mkdtemp()
    try:
        mp = os.path.join(ROOT, "tmp", "regionmap", "regionmap.json")
        if not os.path.isfile(mp):
            print("SKIP: tmp/regionmap/regionmap.json missing")
            return
        rmap, us, jp = RegionMap.load(mp), ElfFile(US), ElfFile(JP)
        verify_inputs(rmap, us, jp)
        out = os.path.join(root, "out")

        log = []
        assert run_translate(Translator(rmap, us, jp), HANDLERS, CONFIG, out, log.append) == 0, log
        assert run_check(Translator(rmap, us, jp), HANDLERS, CONFIG, out, log.append) == 0, log
        assert main(["check", "--out", out]) == 0
        man = json.load(open(os.path.join(out, "manifest.json")))
        assert man["files"]["hooks.json"] == {"kept": 9, "dropped": 0, "rederived": 0}
        assert man["files"]["manual_symbols.json"] == {"kept": 10, "dropped": 0, "rederived": 0}
        ov = json.load(open(os.path.join(out, "overrides.json")))
        assert all(k in ov for k in ("0x0033EC60", "0x0033E708", "0x0033F138"))
        assert all(d["reason"] for d in man["dropped"])
        with open(os.path.join(out, "hooks.json")) as fp:
            assert json.load(fp)["0x0034BCD8"] == "hook_sound_load"

        with open(os.path.join(CONFIG, "ida_db.json")) as fp:
            us_db = json.load(fp)
        with open(os.path.join(out, "ida_db.json")) as fp:
            jp_db = json.load(fp)
        by_ea = {f["ea"]: f for f in jp_db["functions"]}
        mapped = [f for f in rmap.functions if f.jp is not None]
        assert len(jp_db["functions"]) == len(mapped) == len(by_ea)
        for f in rmap.functions:
            if f.us in (0x29A798, 0x2A78A0, 0x2F7490):
                assert f.jp in by_ea and by_ea[f.jp]["chunks"] == [[f.jp, f.jp_end]], hex(f.us)
        # Every switch survives and its JP table holds exactly the JP targets.
        assert len(jp_db["switches"]) == len(us_db["switches"])
        for sw in jp_db["switches"]:
            table = {jp.word(sw["jumps"] + 4 * i) + sw["elbase"] for i in range(sw["ncases"])}
            assert table == set(sw["targets"]), hex(sw["ea"])
        n_seg = len(jp_db["segments"])
        gaps = [d for d in man["dropped"] if d["where"].startswith("segments[")]
        assert n_seg + len(gaps) == len(us_db["segments"])
        lo, hi = jp_db["segments"][0]["start"], jp_db["segments"][-1]["end"]
        assert (jp_db["meta"]["min_ea"], jp_db["meta"]["max_ea"]) == (lo, hi)
        assert jp_db["meta"]["entry"] == jp.entry
        for g, h in zip(jp_db["segments"], jp_db["segments"][1:]):
            assert g["end"] <= h["start"]
        print("ida_db: functions kept %d dropped %d, names kept %d dropped %d, segments %d"
              % (len(jp_db["functions"]), len(us_db["functions"]) - len(jp_db["functions"]),
                 len(jp_db["names"]), len(us_db["names"]) - len(jp_db["names"]), n_seg))
    finally:
        shutil.rmtree(root)


test_lookups()
test_rename()
test_fail_and_drop()
test_duplicates_and_order()
test_verify_inputs()
test_ida_segments()
test_ida_functions_and_names()
test_switch_logic()

if os.path.isfile(US):
    test_real_identity()
    test_real_ida_identity()
if os.path.isfile(US) and os.path.isfile(JP):
    test_real_pair()
    print("PASS: regionconfig (synthetic + real binaries)")
elif os.path.isfile(US):
    print("SKIP: JP binary not present")
    print("PASS: regionconfig (synthetic + US identity)")
else:
    print("SKIP: real binaries not present")
    print("PASS: regionconfig (synthetic)")
