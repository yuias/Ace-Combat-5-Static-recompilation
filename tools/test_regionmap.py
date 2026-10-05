import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

from ps2recomp.elf import ElfFile
from regionmap import FuncMatch, Range, RegionMap, Uncertain
from regionmap.codematch import CodeMatcher, Unit, load_units
from regionmap.mapfile import merge_ranges
from regionmap.common import image_bounds
from regionmap.common import words as read_words
from regionmap.normalize import (REF_ABS, REF_CALL, REF_GP, find_gp, hi_range_for,
                                 Stream, normalize_stream, scan_gp, text_stream)

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


AT, V0, A0, A1, GP = 1, 2, 4, 5, 28
BASE = 0x100000
HI_RANGE = (0x10, 0x48)


def i_type(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def r_type(funct, rs, rt, rd):
    return (rs << 21) | (rt << 16) | (rd << 11) | funct


def lui(rt, imm):
    return i_type(0x0F, 0, rt, imm)


def addiu(rt, rs, imm):
    return i_type(0x09, rs, rt, imm)


def lw(rt, off, base):
    return i_type(0x23, base, rt, off)


def jal(target):
    return (3 << 26) | (target >> 2)


def test_normalize_synthetic():
    # JAL: target masked, call recorded relative to the delay-slot address region.
    s = normalize_stream([jal(0x100800), 0], BASE, HI_RANGE, None)
    assert s.norm == [3 << 26, 0]
    assert s.refs[0] == (REF_CALL, 0x100800)
    assert s.stats["call"] == 1 and s.index(BASE + 4) == 1 and s.addr(1) == BASE + 4
    s = normalize_stream([(2 << 26) | (0x200400 >> 2)], BASE, HI_RANGE, None)
    assert s.norm == [2 << 26] and s.refs[0] == (REF_CALL, 0x200400)

    # lui/addiu pair with a negative low half.
    w = [lui(A0, 0x40), addiu(A0, A0, -0x10)]
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.norm == [w[0] & 0xFFFF0000, w[1] & 0xFFFF0000]
    assert s.refs == {1: (REF_ABS, 0x3FFFF0)}
    assert s.stats["lui"] == 1 and s.stats["abs"] == 1

    # Indexed table access keeps the high half across addu.
    w = [lui(AT, 0x40), r_type(0x21, AT, V0, AT), lw(V0, 0x20, AT)]
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.norm[2] == w[2] & 0xFFFF0000 and s.norm[1] == w[1]
    assert s.refs[2] == (REF_ABS, 0x400020)

    # Float constants (lui outside the image window) are left alone.
    w = [lui(AT, 0x4120), i_type(0x39, AT, 0, 0)]
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.norm == w and not s.refs

    # gp-relative access.
    gp = 0x44F000
    w = [lw(V0, -0x7FF0, GP)]
    s = normalize_stream(w, BASE, HI_RANGE, gp)
    assert s.norm == [w[0] & 0xFFFF0000] and s.refs[0] == (REF_GP, gp - 0x7FF0)
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.norm == w and not s.refs, "gp unknown: not masked"

    # A register overwritten by andi loses its high half.
    w = [lui(A0, 0x40), i_type(0x0C, A0, A0, 0xFF), addiu(A1, A0, 4)]
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.norm[2] == w[2] and not s.refs
    # Same for a load into the register, and a store leaves it alone.
    w = [lui(A0, 0x40), i_type(0x2B, A0, V0, 0), lw(A0, 0, V0), addiu(A1, A0, 4)]
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.refs == {1: (REF_ABS, 0x400000)} and s.norm[3] == w[3]

    # State does not leak past a return (the delay slot still sees it).
    w = [lui(V0, 0x40), r_type(0x08, 31, 0, 0), lw(A1, 4, V0), lw(A0, 8, V0)]
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.refs == {2: (REF_ABS, 0x400004)} and s.norm[3] == w[3]
    # Same after a plain jump.
    w = [lui(V0, 0x40), (2 << 26) | (BASE >> 2), 0, lw(A0, 8, V0)]
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.norm[3] == w[3]
    # A call clears caller-saved registers after its delay slot, but the delay
    # slot itself still pairs with the lui, and saved registers survive.
    s0 = 16
    w = [lui(A0, 0x40), lui(s0, 0x41), jal(0x100800), addiu(A0, A0, -0x10),
         addiu(A0, A0, -0x10), lw(V0, 4, s0)]
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.norm[3] == w[3] & 0xFFFF0000 and s.refs[3] == (REF_ABS, 0x3FFFF0)
    assert s.norm[4] == w[4], "a0 is clobbered by the call"
    assert s.refs[5] == (REF_ABS, 0x410004), "s0 survives the call"
    # JALR clears caller-saved registers too.
    w = [lui(A0, 0x40), r_type(0x09, V0, 0, 31), 0, addiu(A0, A0, 4)]
    s = normalize_stream(w, BASE, HI_RANGE, None)
    assert s.norm[3] == w[3]

    # Two bodies that differ only in addresses normalize identically.
    a = [lui(A0, 0x40), addiu(A0, A0, 0x100), jal(0x100200), 0, lw(V0, 8, A0), 0]
    b = [lui(A0, 0x41), addiu(A0, A0, 0x480), jal(0x100540), 0, lw(V0, 8, A0), 0]
    assert normalize_stream(a, BASE, HI_RANGE, None).norm == \
        normalize_stream(b, BASE, HI_RANGE, None).norm
    c = list(b)
    c[4] = lw(V0, 12, A0)
    assert normalize_stream(a, BASE, HI_RANGE, None).norm != \
        normalize_stream(c, BASE, HI_RANGE, None).norm


class FakeElf:
    def __init__(self, entry, code, symbols=()):
        self.entry = entry
        self.code = code
        self.symbols = list(symbols)

    def section(self, name):
        return None

    def word(self, addr):
        k = (addr - self.entry) >> 2
        return self.code[k] if 0 <= k < len(self.code) else 0


class FakeSym:
    def __init__(self, name, value):
        self.name = name
        self.value = value


def test_find_gp():
    e = FakeElf(0x1000, [lui(GP, 0x45), 0, addiu(GP, GP, -0x1000)])
    assert find_gp(e) == 0x44F000
    e = FakeElf(0x1000, [lui(GP, 0x45), i_type(0x0D, GP, GP, 0x1234)])
    assert find_gp(e) == 0x451234
    # .reginfo wins over the startup code.
    class Sec:
        data = struct.pack("<6I", 0, 0, 0, 0, 0, 0x449000)

    e = FakeElf(0x1000, [lui(GP, 0x45), addiu(GP, GP, -0x1000)])
    e.section = lambda name: Sec if name == ".reginfo" else None
    assert find_gp(e) == 0x449000
    # A register rewritten by another instruction loses its constant.
    e = FakeElf(0x1000, [lui(A0, 0x45), i_type(0x0C, A0, A0, 0xFF),
                         r_type(0x2D, A0, 0, GP)])
    assert scan_gp(e) is None
    # The scan stops at the first call.
    e = FakeElf(0x1000, [lui(A0, 0x45), jal(0x2000), 0, r_type(0x2D, A0, 0, GP)])
    assert scan_gp(e) is None
    # Built in a scratch register and moved into gp, as the entry stub does.
    e = FakeElf(0x1000, [lui(A0, 0x45), 0, addiu(A0, A0, -0x7D90), lui(A1, 0x10),
                         r_type(0x2D, A0, 0, GP)])
    assert find_gp(e) == 0x448270
    # A move from a register with no known value does not count.
    assert find_gp(FakeElf(0x1000, [r_type(0x2D, A0, 0, GP)])) is None
    e = FakeElf(0x1000, [0] * 4, [FakeSym("_gp", 0x123450)])
    assert find_gp(e) == 0x123450
    assert find_gp(FakeElf(0x1000, [0] * 4)) is None
    # addiu before the lui does not count.
    assert find_gp(FakeElf(0x1000, [addiu(GP, GP, 4), lui(GP, 0x45)])) is None


def plain_stream(base, words):
    return Stream(base, list(words), list(words), {}, {})


def test_exact_pass_synthetic():
    seq = lambda hi, n: [hi + k for k in range(n)]
    a, b, c, d, z = seq(0x110, 6), seq(0x120, 6), seq(0x130, 5), seq(0x140, 4), seq(0x150, 2)
    # JP inserts two words between A and B, so B, C and D move by +8.
    us = plain_stream(0x1000, a + b + c + [0, 0] + d + z)
    jp = plain_stream(0x2000, a + [0, 0] + b + c + [0, 0] + d + z)

    def units():
        w = lambda i: 0x1000 + 4 * i
        return [Unit(w(0), w(6), w(0), "fa", True),
                Unit(w(6), w(12), w(6), "fb", True),
                Unit(w(12), w(17), w(12), "fc", True),
                Unit(w(19), w(23), w(19), "fd", True),
                Unit(w(23), w(25), w(6), "fb", False)]   # tail chunk of fb

    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.run()
    ua, ub, uc, ud, uz = cm.units
    assert (ua.status, ua.jp, ua.confidence) == ("same", 0x2000, 1.0), ua
    assert (ub.status, ub.jp, ub.confidence) == ("same", 0x2020, 0.95), ub
    assert (uc.status, uc.jp) == ("same", 0x2038) and ud.jp == 0x2054
    assert uz.status == "unmatched" and uz.jp is None   # below MIN_EXACT_WORDS

    rows = [(r.us_start, r.us_end, r.delta) for r in cm.code_ranges()]
    # B, C, the padding between C and D, and D share one shift and merge.
    assert rows == [(0x1000, 0x1018, 0x1000), (0x1018, 0x105C, 0x1008)], rows
    assert cm.code_translate(0x1018) == 0x2020 and cm.code_translate(0x1030) == 0x2038
    assert cm.code_translate(0x105C) is None and cm.code_translate(0xFFF) is None
    assert list(cm.aligned_pairs())[:2] == [(0, 0), (1, 1)]

    fns = {f.name: f for f in cm.functions()}
    assert fns["fa"].status == "same" and fns["fa"].chunks == []
    # A non-main chunk that is not matched downgrades a matched main chunk.
    assert fns["fb"].status == "body-changed" and fns["fb"].jp == 0x2020
    assert fns["fb"].chunks == [[0x1018, 0x1030, 0x2020, "same"],
                                [0x105C, 0x1064, None, "unmatched"]], fns["fb"].chunks

    # Link order: a unit placed before the end of the previously accepted one
    # is dropped. Walking the units out of US order forces that case.
    cm = CodeMatcher(us, plain_stream(0x2000, us.raw), units(), log=lambda *x: None)
    cm.reps = [cm.units[1], cm.units[0]]
    cm.match_exact()
    assert cm.units[1].status == "same" and cm.units[0].status == "unmatched"
    assert cm.units[0].note == "order" and cm.stats["order_demoted"] == 1

    # Chunks shared by two functions are matched once and both get the result.
    shared = units()
    shared.insert(1, Unit(0x1000, 0x1018, 0x1018, "alias", True))
    cm = CodeMatcher(us, jp, shared, log=lambda *x: None)
    cm.run()
    assert [u.jp for u in cm.units[:2]] == [0x2000, 0x2000]
    assert [(r.us_start, r.us_end) for r in cm.code_ranges()][0] == (0x1000, 0x1018)


def ref_streams(jp_s_target=0x2038, jp_extra=None):
    """US A B S C G D; JP inserts two words before B. S is a 2-word stub whose
    raw words differ (a masked call target) so only A's call reaches it; G is a
    stub with identical raw words."""
    seq = lambda hi, n: [hi + k for k in range(n)]
    jal_s, jal_b = 0x0C000000 | (0x1030 >> 2), 0x0C000000 | (0x1018 >> 2)
    a = [0x110, 0x0C000000, 0x0C000000] + seq(0x113, 3)
    b, c, d = seq(0x120, 6), seq(0x130, 6), seq(0x140, 6)
    s_norm, g = [0x200, 0x201], [0x210, 0x211]
    us_raw = a[:1] + [jal_s, jal_b] + a[3:] + b + s_norm + c + g + d
    us_norm = a + b + s_norm + c + g + d
    us = Stream(0x1000, us_raw, us_norm, {1: (REF_CALL, 0x1030), 2: (REF_CALL, 0x1018)}, {})
    jp_norm = a + [0, 0] + b + s_norm + c + g + d
    jp_raw = list(jp_norm)
    jp_raw[1] = 0x0C000001           # raw words of masked calls differ
    jp_raw[14] = 0x300               # S: raw differs, normalized equal
    jp_refs = {1: (REF_CALL, jp_s_target), 2: (REF_CALL, 0x2060)}
    jp_refs.update(jp_extra or {})
    return us, Stream(0x2000, jp_raw, jp_norm, jp_refs, {})


def test_reference_propagation():
    w = lambda i: 0x1000 + 4 * i
    def units():
        return [Unit(w(0), w(6), w(0), "fa", True), Unit(w(6), w(12), w(6), "fb", True),
                Unit(w(12), w(14), w(12), "stub", True), Unit(w(14), w(20), w(14), "fc", True),
                Unit(w(20), w(22), w(20), "gap", True), Unit(w(22), w(28), w(22), "fd", True)]

    us, jp = ref_streams()
    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.run()
    ua, ub, s, uc, g, ud = cm.units
    assert ub.jp == 0x2020 and uc.jp == 0x2040 and ud.jp == 0x2060, (ub.jp, uc.jp, ud.jp)
    # A 2-word stub is placed only through the JAL of a matched caller.
    assert (s.status, s.method, s.jp, s.jp_end, s.confidence) == \
        ("same", "call", 0x2038, 0x2040, 0.9), s
    # Raw-equal gap between two units at the same shift: the stub in it is placed.
    assert (g.status, g.method, g.jp) == ("same", "exact", 0x2058), g
    # The call to fb disagrees with fb's own placement.
    assert cm.stats["call_conflicts"] == 1 and cm.stats["call_pairs_checked"] == 2, cm.stats
    assert cm.conflicts == [(0x1008, 0x1018, 0x2020, 0x2060)], cm.conflicts
    assert cm.stats["placed_by_call"] == 1 and cm.stats["gap_filled"] == 1
    assert cm.code_translate(w(12)) == 0x2038 and cm.code_translate(w(20)) == 0x2058
    # A cached range table is dropped when propagation changes units.
    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.match_exact()
    before = cm.code_ranges()
    assert cm._ranges is before
    cm.propagate_refs()
    assert cm._ranges is None and cm.code_ranges() is not before

    # A target before the previous matched unit's end breaks link order.
    us, jp = ref_streams(jp_s_target=0x2010)
    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.run()
    assert cm.units[2].status == "unmatched" and cm.units[2].note == "call target out of order"

    # Two call sites naming different targets leave the stub unmatched.
    us, jp = ref_streams(jp_extra={3: (REF_CALL, 0x2040)})
    us.refs[3] = (REF_CALL, 0x1030)
    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.run()
    assert cm.units[2].status == "unmatched" and cm.units[2].note == "call targets disagree"

    # A located unit never survives run().
    us, jp = ref_streams()
    jp.norm[14] = 0x999
    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.run()
    assert cm.units[2].status == "unmatched" and cm.units[2].note == "located but not resolved"
    assert cm.stats["located"] == 1


def test_padding_and_chunk_confidence():
    # Padding next to a body-changed unit is kept: the unit resets the anchor.
    us = plain_stream(0x1000, [1, 2, 3, 4, 5, 6, 0, 0, 7, 8, 9, 10])
    jp = plain_stream(0x2000, [1, 2, 3, 4, 50, 60, 0, 0, 7, 8, 9, 10])
    a = Unit(0x1000, 0x1010, 0x1000, "a", True, 0x2000, 0x2010, "same", "exact", 1.0, 1.0)
    b = Unit(0x1010, 0x1018, 0x1010, "b", True, 0x2010, 0x2018, "body-changed", "diff", 0.7, 0.7)
    c = Unit(0x1020, 0x1030, 0x1020, "c", True, 0x2020, 0x2030, "same", "exact", 1.0, 1.0)
    cm = CodeMatcher(us, jp, [a, b, c], log=lambda *x: None)
    assert [(r.us_start, r.us_end) for r in cm.code_ranges()] ==         [(0x1000, 0x1010), (0x1018, 0x1030)]

    # A downgraded function carries the lowest chunk confidence.
    t = Unit(0x1030, 0x1038, 0x1000, "a", False, status="unmatched")
    cm = CodeMatcher(us, jp, [a, t], log=lambda *x: None)
    f = cm.functions()[0]
    assert f.status == "body-changed" and f.confidence == 0.0, f


def test_load_units():
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "db.json")
        with open(path, "w") as fp:
            json.dump({"functions": [
                {"ea": 0x200, "name": "f1", "chunks": [[0x200, 0x210], [0x100, 0x110]]},
                {"ea": 0x300, "name": "f2", "chunks": [[0x300, 0x2F0], [0x300, 0x320]]},
                {"ea": 0x5000, "name": "f3", "chunks": [[0x5000, 0x5010]]}]}, fp)
        us = load_units(path, 0x100, 0x400)
        # Empty and out-of-text chunks are skipped; the rest are sorted.
        assert [(u.us, u.name, u.main) for u in us] == \
            [(0x100, "f1", False), (0x200, "f1", True), (0x300, "f2", True)]
    finally:
        shutil.rmtree(d)


def run_tool(*args):
    env = dict(os.environ, PYTHONPATH=TOOLS)
    r = subprocess.run([sys.executable, "-m", "regionmap", *args], env=env, cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_real_normalize():
    us, jp = ElfFile(US), ElfFile(JP)
    gps = []
    for elf in (us, jp):
        gp = find_gp(elf)
        assert gp is not None
        # The startup scan and .reginfo must agree.
        assert scan_gp(elf) == gp
        gps.append(gp)
    print("gp: us=%08X jp=%08X delta=%+#x" % (gps[0], gps[1], gps[1] - gps[0]))

    # Normalizing a chunk on its own must equal the same slice of the whole
    # stream, i.e. no state leaks across function boundaries.
    hi_range = hi_range_for(image_bounds(us, jp))
    whole = text_stream(us, hi_range, gps[0])
    lo, hi = us.text_ranges()[0]
    with open(os.path.join(ROOT, "config", "ida_db.json")) as fp:
        funcs = json.load(fp)["functions"]
    total = bad = 0
    for f in funcs:
        for a, b in f["chunks"]:
            if a < lo or b > hi or b <= a:
                continue
            total += 1
            alone = normalize_stream(read_words(us, a, b), a, hi_range, gps[0])
            i = whole.index(a)
            if alone.norm != whole.norm[i:i + (b - a) // 4]:
                bad += 1
    print("standalone chunks differing from whole stream: %d of %d" % (bad, total))
    assert total > 1000 and bad <= 5, (bad, total)


def test_real_binaries():
    out = tempfile.mkdtemp()
    try:
        text = run_tool("build", US, JP, "--out", out)
        assert "elapsed" in text
        norm_lines = [l for l in text.splitlines() if l.startswith("normalize ")]
        assert len(norm_lines) == 2, text
        for l in norm_lines:
            m = re.match(r"normalize \S+: \d+ words, call=(\d+) lui=(\d+) abs=(\d+) "
                         r"gp=(\d+) masked, gp=([0-9A-F]{8}), ([\d.]+) s$", l)
            assert m, l
            assert all(int(m.group(k)) > 0 for k in (1, 2, 3, 4)), l
            assert float(m.group(6)) <= 10.0, l
        path = os.path.join(out, "regionmap.json")
        m = RegionMap.load(path)
        m.validate()
        assert m.us_info["file"] == "SLUS_208.51" and m.jp_info["file"] == "SLPS_254.18"
        assert len(m.us_info["sha256"]) == 64
        assert m.us_info["gp"] and m.jp_info["gp"]

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

        code = [l for l in text.splitlines() if l.startswith("code: ")]
        assert len(code) == 1, text
        mc = re.match(r"code: (\d+) functions \((\d+) units\): same (\d+), "
                      r"body-changed (\d+), unmatched (\d+); ([\d.]+) s$", code[0])
        assert mc, code[0]
        nfunc, same, bc, un = (int(mc.group(k)) for k in (1, 3, 4, 5))
        assert same >= 6000 and same + bc + un == nfunc, code[0]
        assert len(m.functions) == nfunc
        calls = [l for l in text.splitlines() if l.startswith("calls: ")]
        mk = re.match(r"calls: (\d+) pairs checked, (\d+) conflicts \(([\d.]+)%\); "
                      r"placed by call (\d+), located (\d+), gap-filled (\d+)$",
                      calls[0] if calls else "")
        assert mk, text
        pairs, conflicts = int(mk.group(1)), int(mk.group(2))
        assert pairs > 1000 and conflicts * 200 <= pairs, calls[0]
        assert un < 764, code[0]
        assert m.translate(0x31C138) == 0x31C460
        entry = run_tool("lookup", path, "0x31C138").split()
        assert entry[:4] == ["0031C138", "->", "0031C460", ".text"] and entry[4] == "same"
    finally:
        shutil.rmtree(out)


test_merge_ranges()
test_validate_translate()
test_json_roundtrip()
test_normalize_synthetic()
test_find_gp()
test_exact_pass_synthetic()
test_reference_propagation()
test_padding_and_chunk_confidence()
test_load_units()

if os.path.isfile(US) and os.path.isfile(JP):
    test_real_normalize()
    test_real_binaries()
    print("PASS: regionmap (synthetic + real binaries)")
else:
    print("SKIP: real binaries not present")
    print("PASS: regionmap (synthetic)")
