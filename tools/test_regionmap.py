import json
import os
import shutil
import subprocess
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

from regionmap import FuncMatch, Range, RegionMap, Uncertain
from regionmap.mapfile import merge_ranges

US = os.path.join(ROOT, "tmp", "us", "SLUS_208.51")
JP = os.path.join(ROOT, "tmp", "jp", "SLPS_254.18")


def raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return True
    return False


def make_func(us, us_end, name, chunks=None):
    return FuncMatch(us, us_end, name, us + 8, us_end + 8, "same", "exact", 1.0, 1.0,
                     "", chunks or [])


def test_merge_ranges():
    rows = [Range(0x20, 0x30, 8, ".text", "func", "high"),
            Range(0x10, 0x20, 8, ".text", "diff", "medium"),
            Range(0x40, 0x50, 8, ".text", "func", "high")]
    out = merge_ranges(rows)
    assert [(r.us_start, r.us_end) for r in out] == [(0x10, 0x30), (0x40, 0x50)]
    assert out[0].source == "diff" and out[0].confidence == "medium"
    assert rows[1].us_end == 0x20, "input rows must not be mutated"

    # Same delta but a different section does not merge.
    out = merge_ranges([Range(0, 8, 4, ".a", "func", "high"),
                        Range(8, 16, 4, ".b", "func", "high")])
    assert len(out) == 2

    assert raises(ValueError, merge_ranges,
                  [Range(0, 16, 4, ".text", "func", "high"),
                   Range(8, 24, 8, ".text", "func", "high")])
    assert raises(ValueError, merge_ranges,
                  [Range(0, 16, 4, ".a", "func", "high"),
                   Range(8, 24, 4, ".b", "func", "high")])
    out = merge_ranges([Range(8, 8, 4, ".a", "func", "high"),
                        Range(0, 4, 4, ".a", "func", "high")])
    assert [(r.us_start, r.us_end) for r in out] == [(0, 4)], "empty rows are dropped"
    out = merge_ranges([Range(0, 16, 4, ".text", "func", "high"),
                        Range(8, 24, 4, ".text", "func", "low")])
    assert len(out) == 1 and (out[0].us_end, out[0].confidence) == (24, "low")


def make_map():
    ranges = [Range(0x100, 0x200, 0x10, ".text", "func", "high"),
              Range(0x300, 0x400, 0x20, ".data", "content", "medium")]
    unc = [Uncertain(0x200, 0x240, ".text", [0x10, 0x14], "content differs")]
    funcs = [make_func(0x100, 0x140, "sub_100"),
             make_func(0x140, 0x180, "sub_140", [[0x140, 0x180, 0x150, "same"],
                                                  [0x1C0, 0x1E0, 0x1D0, "same"]])]
    secs = [{"name": ".text", "us_start": 0x100, "us_end": 0x240,
             "jp_start": 0x110, "jp_end": 0x250, "nobits": False}]
    return RegionMap({"file": "a"}, {"file": "b"}, secs, ranges, unc, funcs, {"k": 1})


def test_validate_translate():
    m = make_map()
    m.validate()

    assert m.translate(0x100) == 0x110
    assert m.translate(0x1FF) == 0x20F
    assert m.translate(0x200) is None, "us_end is exclusive"
    assert m.translate(0xFF) is None
    assert m.translate(0x250) is None, "gap between ranges"
    assert m.translate(0x300) == 0x320
    assert m.translate(0x400) is None
    assert m.range_for(0x210) is None
    assert m.uncertain_for(0x210).reason == "content differs"
    assert m.uncertain_for(0x240) is None and m.uncertain_for(0x1FF) is None

    assert m.function_at(0x150).name == "sub_140"
    assert m.function_at(0x1D0).name == "sub_140", "non-main chunk"
    assert m.function_at(0x1A0) is None
    hit = m.function_chunk_at(0x1D4)
    assert hit[0].name == "sub_140" and hit[1] == 0x1C0

    bad = make_map()
    bad.uncertain.append(Uncertain(0x1F0, 0x210, ".text", [], "overlap"))
    assert raises(ValueError, bad.validate)

    bad = make_map()
    bad.uncertain.append(Uncertain(0x230, 0x260, ".text", [], "overlaps another"))
    assert raises(ValueError, bad.validate)

    bad = make_map()
    bad.ranges.append(Range(0x150, 0x160, 0, ".text", "func", "high"))
    assert raises(ValueError, bad.validate)

    bad = make_map()
    bad.uncertain = [Uncertain(0x500, 0x500, ".text", [], "empty")]
    assert raises(ValueError, bad.validate)


def test_json_roundtrip():
    m = make_map()
    m2 = RegionMap.from_json(json.loads(json.dumps(m.to_json())))
    assert m2.to_json() == m.to_json()
    assert m2.ranges == m.ranges and m2.uncertain == m.uncertain
    assert m2.functions == m.functions

    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "sub", "map.json")
        m.save(path)
        m3 = RegionMap.load(path)
        assert m3.to_json() == m.to_json()
    finally:
        shutil.rmtree(d)


def run_tool(*args):
    env = dict(os.environ, PYTHONPATH=TOOLS)
    r = subprocess.run([sys.executable, "-m", "regionmap", *args], env=env, cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_real_binaries():
    out = tempfile.mkdtemp()
    try:
        text = run_tool("build", US, JP, "--out", out)
        assert "elapsed" in text
        path = os.path.join(out, "regionmap.json")
        m = RegionMap.load(path)
        m.validate()
        assert m.us_info["file"] == "SLUS_208.51" and m.jp_info["file"] == "SLPS_254.18"
        assert len(m.us_info["sha256"]) == 64

        secs = {s["name"]: s for s in m.sections}
        for name in (".text", ".vutext", ".data", ".rodata", ".sdata", ".sbss", ".bss"):
            assert name in secs, name
        def size(s):
            return s["us_end"] - s["us_start"], s["jp_end"] - s["jp_start"]

        vu = secs[".vutext"]
        assert size(vu) == (0x2EB20, 0x2EB20)
        tx = secs[".text"]
        assert size(tx)[1] - size(tx)[0] == 0x340
        assert secs[".data"]["jp_start"] - secs[".data"]["us_start"] == 0x380
        assert secs[".sbss"]["nobits"] and secs[".bss"]["nobits"]

        assert m.translate(0x3C7F00) == 0x3C8280
        assert m.translate(0x399310) == 0x399650

        lines = run_tool("lookup", path, "0x3C7F00", "0x399310").splitlines()
        assert lines[0].startswith("003C7F00 -> 003C8280  .data"), lines[0]
        assert lines[1].startswith("00399310 -> 00399650  .vutext"), lines[1]
        # Addresses are always hex, with or without the 0x prefix.
        assert run_tool("lookup", path, "3C7F00") == run_tool("lookup", path, "0x3C7F00")
        assert run_tool("lookup", path, "446100").startswith("00446100 -> 00446480")
        assert run_tool("lookup", path, "0x1").startswith("00000001 -> unmapped")
    finally:
        shutil.rmtree(out)


test_merge_ranges()
test_validate_translate()
test_json_roundtrip()

if os.path.isfile(US) and os.path.isfile(JP):
    test_real_binaries()
    print("PASS: regionmap (synthetic + real binaries)")
else:
    print("SKIP: real binaries not present")
    print("PASS: regionmap (synthetic)")
