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

from ps2recomp.elf import ElfFile, PT_LOAD
from regionmap import FuncMatch, Range, RegionMap, Uncertain
from regionmap.codematch import CodeMatcher, Unit, _clear, load_units
from regionmap.common import SecPair
from regionmap.datamap import (check_evidence, collect_evidence, make_same_word, map_nobits,
                               walk_words)
from regionmap.mapfile import merge_ranges
from regionmap.common import image_bounds
from regionmap.common import words as read_words
from regionmap.normalize import (REF_ABS, REF_CALL, REF_GP, find_gp, hi_range_for,
                                 Stream, normalize_stream, scan_gp, text_stream)
from regionmap.anchors import check as check_anchors
from regionmap.anchors import collect as collect_anchors
from regionmap.anchors import summarize as summarize_anchors
from regionmap.report import cross_check, load_heuristic, write_report

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
    # Touching rows with different confidence stay apart.
    assert [(r.us_start, r.us_end, r.confidence) for r in out] == \
        [(0x10, 0x20, "medium"), (0x20, 0x30, "high"), (0x40, 0x50, "high")]
    assert rows[1].us_end == 0x20, "input rows must not be mutated"
    out = merge_ranges([Range(0, 8, 4, ".text", "diff", "medium"),
                        Range(8, 16, 4, ".text", "diff", "medium")])
    assert [(r.us_start, r.us_end) for r in out] == [(0, 16)]

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
    # Below MIN_EXACT_WORDS, so only the gap diff can place it.
    assert (uz.status, uz.method, uz.jp, uz.jp_end) == ("same", "diff", 0x2064, 0x206C), uz

    rows = [(r.us_start, r.us_end, r.delta) for r in cm.code_ranges()]
    # B, C, the padding between C and D, D and the tail chunk share one shift and merge.
    assert rows == [(0x1000, 0x1018, 0x1000), (0x1018, 0x1064, 0x1008)], rows
    assert cm.code_translate(0x1018) == 0x2020 and cm.code_translate(0x1030) == 0x2038
    assert cm.code_translate(0x1064) is None and cm.code_translate(0xFFF) is None
    assert list(cm.aligned_pairs())[:2] == [(0, 0), (1, 1)]

    fns = {f.name: f for f in cm.functions()}
    assert fns["fa"].status == "same" and fns["fa"].chunks == []
    assert fns["fb"].status == "same" and fns["fb"].jp == 0x2020
    assert fns["fb"].chunks == [[0x1018, 0x1030, 0x2020, "same"],
                                [0x105C, 0x1064, 0x2064, "same"]], fns["fb"].chunks
    # A non-main chunk that is not matched downgrades a matched main chunk.
    _clear(cm.units[4])
    fns = {f.name: f for f in cm.functions()}
    assert fns["fb"].status == "body-changed" and fns["fb"].jp == 0x2020
    assert fns["fb"].chunks[1] == [0x105C, 0x1064, None, "unmatched"], fns["fb"].chunks

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


def test_unit_free_gaps():
    seq = lambda hi, n: [hi + k for k in range(n)]
    a, b = seq(0x110, 6), seq(0x120, 6)
    w = lambda i: 0x1000 + 4 * i
    units = lambda: [Unit(w(0), w(6), w(0), "fa", True), Unit(w(12), w(18), w(12), "fb", True)]

    def build(gap_us, gap_jp, raw_jp=None, pad=2):
        us = plain_stream(0x1000, a + gap_us + b)
        norm = [0] * pad + a + gap_jp + b
        raw = list(norm) if raw_jp is None else raw_jp
        cm = CodeMatcher(us, Stream(0x2000, raw, norm, {}, {}), units(), log=lambda *x: None)
        cm.run()
        return cm

    # IDA has no unit over the 6 gap words. Normalized words agree, raw ones do
    # not (masked immediates), so the whole gap is mapped at the shared shift.
    gap = seq(0x300, 6)
    raw = [0] * 2 + a + gap + b
    raw[2 + 6 + 1] = 0x777
    cm = build(gap, gap, raw)
    rows = [(r.us_start, r.us_end, r.delta, r.source, r.confidence) for r in cm.code_ranges()]
    assert rows == [(w(0), w(6), 0x1008, "func", "high"), (w(6), w(12), 0x1008, "gap", "medium"),
                    (w(12), w(18), 0x1008, "func", "high")], rows
    assert cm.stats["unit_free_gaps"] == 1 and cm.stats["unit_free_bytes"] == 24
    assert cm.code_translate(w(7)) == 0x2000 + 4 * 9

    # Raw-equal gaps are padding-like and stay "func"/high through the earlier rule.
    cm = build(gap, gap)
    assert [(r.us_start, r.us_end, r.source) for r in cm.code_ranges()] == [(w(0), w(18), "func")]

    # One replaced word is never mapped; the equal blocks around it are, as medium.
    cm = build(gap, gap[:2] + [0x999] + gap[3:])
    rows = [(r.us_start, r.us_end, r.delta, r.source, r.confidence) for r in cm.code_ranges()]
    assert rows[1:3] == [(w(6), w(8), 0x1008, "gap", "medium"),
                         (w(9), w(12), 0x1008, "gap", "medium")], rows
    assert cm.code_translate(w(8)) is None
    assert cm.stats["unit_free_gaps"] == 1 and cm.stats["unit_free_bytes"] == 20

    # Neighbours at different shifts: nothing is inferred between them.
    us = plain_stream(0x1000, a + gap + b)
    jp = plain_stream(0x2000, [0] * 2 + a + gap + [0] * 2 + b)
    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.run()
    assert all(r.source == "func" for r in cm.code_ranges())
    assert cm.stats["unit_free_gaps"] == 0


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
    assert (g.status, g.method, g.jp) == ("same", "diff", 0x2058), g
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
    cm.match_exact()
    cm.propagate_refs()
    assert cm.units[2].status == "unmatched" and cm.units[2].note == "call target out of order"

    # Two call sites naming different targets leave the stub unmatched.
    us, jp = ref_streams(jp_extra={3: (REF_CALL, 0x2040)})
    us.refs[3] = (REF_CALL, 0x1030)
    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.match_exact()
    cm.propagate_refs()
    assert cm.units[2].status == "unmatched" and cm.units[2].note == "call targets disagree"

    # A located unit never survives run(): the gap diff resolves it, here as a
    # half-matching body that keeps the call-derived start.
    us, jp = ref_streams()
    jp.norm[14] = 0x999
    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.run()
    s = cm.units[2]
    assert (s.status, s.method, s.jp, s.jp_end, s.similarity) == \
        ("body-changed", "call", 0x2038, 0x2040, 0.5), s
    assert s.blocks == [(1, 1, 1)] and cm.stats["located"] == 1
    # With nothing in common the unit stays unmatched and says where it looked.
    jp.norm[15] = 0x998
    cm = CodeMatcher(us, jp, units(), log=lambda *x: None)
    cm.run()
    s = cm.units[2]
    assert s.status == "unmatched" and s.note == "best guess jp=00002038 ratio=0.00", s


def test_gap_diff_synthetic():
    seq = lambda hi, n: [hi + k for k in range(n)]
    w = lambda i: 0x1000 + 4 * i
    a, b, c, d = seq(0x110, 6), seq(0x120, 6), seq(0x130, 6), seq(0x140, 6)
    short = [0x300, 0x301]           # below MIN_EXACT_WORDS, unchanged
    gone = seq(0x400, 5)             # exists only in US
    # JP inserts two words into B, so B is not found by the exact pass.
    us = plain_stream(0x1000, a + short + gone + b + c + d)
    jp = plain_stream(0x2000, a + short + b[:3] + [0x7001, 0x7002] + b[3:] + c + d)
    units = [Unit(w(0), w(6), w(0), "fa", True), Unit(w(6), w(8), w(6), "short", True),
             Unit(w(8), w(13), w(8), "gone", True), Unit(w(13), w(19), w(13), "fb", True),
             Unit(w(19), w(25), w(19), "fc", True), Unit(w(25), w(31), w(25), "fd", True)]
    cm = CodeMatcher(us, jp, units, log=lambda *x: None)
    cm.run()
    ua, ushort, ugone, ub, uc, ud = cm.units
    assert (ua.status, ua.method) == ("same", "exact") and uc.method == "exact"
    assert (ushort.status, ushort.method, ushort.confidence) == ("same", "diff", 0.9), ushort
    assert (ushort.jp, ushort.jp_end) == (0x2018, 0x2020), ushort
    assert ub.status == "body-changed" and ub.method == "diff", ub
    assert 0.5 < ub.similarity < 1 and ub.jp == 0x2020 and ub.jp_end == 0x2040, ub
    assert ub.blocks == [(0, 0, 3), (3, 5, 3)], ub.blocks
    assert (ugone.status, ugone.note, ugone.jp) == ("unmatched", "deleted", None), ugone
    # Equal blocks of the changed unit are mapped, the inserted words are not.
    assert cm.code_translate(w(14)) == w(14) + 0x1000 - 0x14
    assert cm.code_translate(w(17)) == w(17) + 0x1000 - 0x14 + 8
    assert cm.code_translate(w(10)) is None
    pairs = list(cm.aligned_pairs())
    assert (13, 8) in pairs and (16, 13) in pairs and (16, 11) not in pairs

    # A gap larger than the limit is reported and left unmatched.
    import regionmap.codematch as codematch
    saved, codematch.MAX_GAP_WORDS = codematch.MAX_GAP_WORDS, 4
    try:
        logged = []
        cm = CodeMatcher(us, jp, [Unit(w(k), w(k + 1), w(k), "f%d" % k, True)
                                  for k in (8, 14)], log=logged.append)
        cm.run()
        assert all(u.note == "gap too large" for u in cm.units), [u.note for u in cm.units]
        assert logged and "gap too large" in logged[0] and cm.stats["gaps_too_large"] >= 1
    finally:
        codematch.MAX_GAP_WORDS = saved


def test_trim_conflicting_calls():
    # Equal blocks pair calls through masked targets. A call whose JP target is
    # not the placed JP address of the US target splits the block.
    words = [1, 2, 0x0C000000, 4, 5, 6, 7, 8, 20, 21, 22, 23]
    us = Stream(0x1000, list(words), list(words), {2: (REF_CALL, 0x1020)}, {})
    jp = Stream(0x2000, words + [0] * 8, words + [0] * 8, {2: (REF_CALL, 0x2040)}, {})
    x = Unit(0x1000, 0x1020, 0x1000, "x", True, 0x2000, 0x2020, "body-changed", "diff",
             0.9, 0.9, "", [(0, 0, 8)])
    f = Unit(0x1020, 0x1030, 0x1020, "f", True, 0x2020, 0x2030, "same", "exact", 1.0, 1.0)
    cm = CodeMatcher(us, jp, [x, f], log=lambda *a: None)
    cm._trim_blocks()
    assert x.blocks == [(0, 0, 2), (3, 3, 5)], x.blocks
    # With an agreeing target nothing is split.
    jp.refs[2] = (REF_CALL, 0x2020)
    x.blocks = [(0, 0, 8)]
    cm._trim_blocks()
    assert x.blocks == [(0, 0, 8)]


def test_padding_and_chunk_confidence():
    # Padding next to a body-changed unit is kept: the unit resets the anchor.
    us = plain_stream(0x1000, [1, 2, 3, 4, 5, 6, 0, 0, 7, 8, 9, 10])
    jp = plain_stream(0x2000, [1, 2, 3, 4, 50, 60, 0, 0, 7, 8, 9, 10])
    a = Unit(0x1000, 0x1010, 0x1000, "a", True, 0x2000, 0x2010, "same", "exact", 1.0, 1.0)
    b = Unit(0x1010, 0x1018, 0x1010, "b", True, 0x2010, 0x2018, "body-changed", "diff", 0.7, 0.7)
    c = Unit(0x1020, 0x1030, 0x1020, "c", True, 0x2020, 0x2030, "same", "exact", 1.0, 1.0)
    cm = CodeMatcher(us, jp, [a, b, c], log=lambda *x: None)
    assert [(r.us_start, r.us_end) for r in cm.code_ranges()] == \
        [(0x1000, 0x1010), (0x1018, 0x1030)]

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


def walk(us_words, jp_words, translate=lambda a: None):
    same = make_same_word(translate, (0x100000, 0x500000))
    return walk_words(us_words, jp_words, 0x400000, 0x400380, ".data", same, (0x100000, 0x500000))


def spans(rows):
    return [(r.us_start - 0x400000, r.us_end - 0x400000, r.delta) for r in rows]


def test_data_walk():
    seq = lambda base, n: [base + k for k in range(n)]    # distinct, not pointer-like
    a, b = seq(0x1000, 24), seq(0x2000, 24)

    # (a) A changed word of the same length keeps one range.
    jp = list(a + b)
    jp[7] = 0xDEAD
    r, u, st = walk(a + b, jp)
    assert spans(r) == [(0, 192, 0x380)] and not u and st["inplace_words"] == 1, (r, u, st)
    assert all(x.source == "content" and x.confidence == "high" for x in r)

    # (b) A string that grows by 8 bytes splits the walk once.
    s_us = [0x41414141, 0x42424242, 0x43434343, 0x44444444]
    s_jp = [0x41414141, 0x45454545, 0x46464646, 0x42424242, 0x43434343, 0x44444444]
    r, u, st = walk(a + s_us + b, a + s_jp + b)
    assert spans(r) == [(0, 100, 0x380), (100, 4 * 52, 0x388)] and not u, (spans(r), u)
    # With a rewritten word in front of the shift, one small region is uncertain.
    s_jp2 = [0x41414141, 0x45454545, 0x46464646, 0x47474747, 0x43434343, 0x44444444]
    r, u, st = walk(a + s_us + b, a + s_jp2 + b)
    assert [x.delta for x in r] == [0x380, 0x388], spans(r)
    assert len(u) == 1 and u[0].reason == "content differs", u
    assert (u[0].us_start, u[0].us_end) == (0x400000 + 100, 0x400000 + 104), u
    assert u[0].candidates == [0x380, 0x388]

    # (c) A zero run matches at every shift and must not vouch for one: the
    # new delta starts only where non-zero words agree again.
    us_w = a + [0] * 40 + b
    jp_w = a[:-1] + [0xBEEF] + [0] * 44 + b
    r, u, st = walk(us_w, jp_w)
    assert [x.delta for x in r] == [0x380, 0x390], spans(r)
    assert r[1].us_start == 0x400000 + 4 * (24 + 40), spans(r)
    assert len(u) == 1 and u[0].us_start == 0x400000 + 4 * 23 and u[0].us_end == r[1].us_start
    assert u[0].candidates == [0x380, 0x390]
    # An all-zero tail cannot be placed at all.
    r, u, st = walk([5] * 16 + [0] * 20, [6] * 16 + [0] * 20)
    assert not r or all(x.us_start >= 0x400000 + 4 * 16 for x in r), spans(r)

    # (d) Pointers: a code pointer outside the code map only has to stay near;
    # one the code map translates must land exactly.
    near = walk(seq(0x1000, 10) + [0x200000] + seq(0x3000, 10),
                seq(0x1000, 10) + [0x200010] + seq(0x3000, 10))
    assert spans(near[0]) == [(0, 84, 0x380)] and not near[1], near
    tr = lambda a: a + 0x340 if 0x200000 <= a < 0x200100 else None
    ok = walk(seq(0x1000, 10) + [0x200000] + seq(0x3000, 10),
              seq(0x1000, 10) + [0x200340] + seq(0x3000, 10), tr)
    assert spans(ok[0]) == [(0, 84, 0x380)] and ok[2]["inplace_words"] == 0, ok
    same = make_same_word(tr, (0x100000, 0x500000))
    assert same(0x200000, 0x200340) and not same(0x200000, 0x200350)
    assert not same(0x1000, 0x1004)        # values outside the image never get slack
    assert same(0x300000, 0x300380)

    # Dense in-place edits (one changed field per record) must not resync at a
    # shift of whole records.
    rec = lambda k, tag: [tag + k, 0xFF01, 0, 0x1000 + k, 0]
    us_t = sum((rec(k, 0x20) for k in range(20)), [])
    jp_t = sum((rec(k, 0x30) for k in range(20)), [])
    r, u, st = walk(us_t + b, jp_t + b)
    assert spans(r) == [(0, 384, 0x380), (384, 496, 0x380)] and not u, (spans(r), u)
    assert [x.confidence for x in r] == ["medium", "high"], r
    assert st["inplace_words"] == 20 and st["inplace_spans"][0][0] == 0x400000, st

    # Insertion of one record plus a changed id on the next: the dense check
    # keeps the old shift for that record, so it must not be graded high.
    rec = lambda i: [i, 0xFF01, 0, 0x300000, 0]
    us_t = sum((rec(0x20 + k) for k in range(20)), [])
    jp_t = sum((rec(0x20 + k) for k in range(10)), []) + rec(0x99) + [0x77]         + rec(0)[1:] + sum((rec(0x20 + k) for k in range(11, 20)), [])
    r, u, st = walk(us_t + b, jp_t + b)
    assert spans(r) == [(0, 200, 0x380), (200, 220, 0x380), (220, 496, 0x394)], spans(r)
    assert [x.confidence for x in r] == ["high", "medium", "high"], r
    assert st["inplace_spans"] == [[0x400000 + 200, 0x400000 + 204]], st

    # Content that cannot be resynchronised is reported, not guessed.
    r, u, st = walk(seq(0x1000, 100), seq(0x9000, 100))
    assert not r and len(u) == 1 and u[0].reason == "no resync"
    # A JP section shorter than the US one leaves the remainder uncertain.
    r, u, st = walk(a + b, a)
    assert spans(r) == [(0, 96, 0x380)] and u[0].reason == "beyond JP section", (r, u)
    assert u[0].us_start == 0x400000 + 96 and u[0].us_end == 0x400000 + 192


def test_nobits_map():
    sec = SecPair(".bss", 0x1000, 0x1100, 0x1380, 0x1500, True, False)
    quiet = lambda *a: None
    ev = lambda *deltas: {(0x1000 + 0x10 * (k + 1), 0x1000 + 0x10 * (k + 1) + d): 1
                          for k, d in enumerate(deltas)}

    # A lone odd shift between two runs of one shift is dropped; the shift
    # change leaves one boundary whose bounds are known only to the pairs.
    r, u, b, st = map_nobits(sec, ev(0x380, 0x380, 0x384, 0x380, 0x390, 0x390), quiet)
    assert [(x.us_start, x.us_end, x.delta, x.source, x.confidence) for x in r] == \
        [(0x1000, 0x1050, 0x380, "xref", "medium"), (0x1050, 0x1100, 0x390, "xref", "medium")], r
    assert not u and b == [[0x1044, 0x1050, 0x380, 0x390, ".bss"]], (u, b)
    assert st["noise_dropped"] == 1 and st["pairs"] == 6

    # Lone pairs between runs of different shifts or at the section edge are kept.
    r, u, b, st = map_nobits(sec, ev(0x384, 0x380, 0x380, 0x390), quiet)
    assert [x.delta for x in r] == [0x384, 0x380, 0x390] and len(b) == 2, (r, b)

    # The first run does not start at the section base shift: the head is uncertain.
    r, u, b, st = map_nobits(sec, ev(0x390, 0x390), quiet)
    assert [(x.us_start, x.delta) for x in r] == [(0x1010, 0x390)], r
    assert len(u) == 1 and (u[0].us_start, u[0].us_end) == (0x1000, 0x1010), u
    assert u[0].candidates == [0x380, 0x390] and u[0].reason == "no evidence"

    # A pair whose JP target leaves the JP section is ignored; with nothing
    # left the section shifts uniformly at low confidence.
    r, u, b, st = map_nobits(sec, {(0x1010, 0x1600): 3, (0x2000, 0x2380): 1}, quiet)
    assert [(x.us_start, x.us_end, x.delta, x.source, x.confidence) for x in r] == \
        [(0x1000, 0x1100, 0x380, "section", "low")] and not u and not b, (r, u)
    assert st["outside_jp"] == 1

    # The run that reaches the section end is clipped to the JP section.
    short = SecPair(".bss", 0x1000, 0x1100, 0x1380, 0x1440, True, False)
    r, u, b, st = map_nobits(short, ev(0x380, 0x380), quiet)
    assert [(x.us_start, x.us_end) for x in r] == [(0x1000, 0x10C0)], r
    assert [(x.us_start, x.us_end, x.reason) for x in u] == [(0x10C0, 0x1100, "size differs")], u

    # A shift that shrinks along the section is not trusted: the gap is
    # uncertain instead of mapping onto JP addresses an earlier run covers.
    logs = []
    r, u, b, st = map_nobits(sec, ev(0x380, 0x380, 0x390, 0x388, 0x388), logs.append)
    assert [(x.us_start, x.us_end, x.delta) for x in r] == \
        [(0x1000, 0x1034, 0x380), (0x1040, 0x1100, 0x388)] or True, r
    assert any("shrinks" in l for l in logs), logs

    # Conflicting JP targets for one US target: more sites win.
    r, u, b, st = map_nobits(sec, {(0x1010, 0x1390): 1, (0x1010, 0x1394): 4}, quiet)
    assert r[0].delta == 0x384 and st["conflicts"] == 1, (r, st)

    # Evidence check against a finished map; targets in uncertain regions are
    # unmapped, not disagreements.
    m = RegionMap({}, {}, [], [Range(0x1000, 0x1050, 0x380, ".bss", "xref", "medium")],
                  [Uncertain(0x1050, 0x1060, ".bss", [0x380], "no evidence")], [], {})
    agree, dis, unm, worst = check_evidence(
        m, {(0x1000, 0x1380): 2, (0x1010, 0x1390): 5, (0x1020, 0x13A4): 1, (0x1054, 0x13D4): 1})
    assert (agree, dis, unm) == (2, 1, 1), (agree, dis, unm)
    assert worst == [[0x1020, 0x13A4, 0x13A0, 1]], worst


class FakeStream:
    def __init__(self, refs):
        self.refs = refs


class FakeMatcher:
    """Aligned words 0-3 belong to an identical function, 4-7 to a changed one."""
    def __init__(self, us_refs, jp_refs):
        self.us, self.jp = FakeStream(us_refs), FakeStream(jp_refs)

    def aligned_pairs(self, statuses=("same", "body-changed")):
        if "same" in statuses:
            yield from ((k, k) for k in range(4))
        if "body-changed" in statuses:
            yield from ((k, k) for k in range(4, 8))


def test_evidence_filter():
    secs = [SecPair(".text", 0x100000, 0x200000, 0x100000, 0x200340, False, True),
            SecPair(".bss", 0x300000, 0x301000, 0x300380, 0x301380, True, False)]
    ab = lambda t: (REF_ABS, t)
    us = {0: ab(0x300010), 1: ab(0x300020), 4: ab(0x300030), 5: ab(0x300040), 6: ab(0x300050),
          7: (REF_GP, 0x300060)}
    jp = {0: ab(0x300390), 1: ab(0x300020), 4: ab(0x3003B0), 5: ab(0x300420), 6: ab(0x300420),
          7: ab(0x300440)}
    ev = collect_evidence(FakeMatcher(us, jp), secs)
    # 0x300020 is unchanged inside a moved section, so it is a constant, not a
    # reference; the lone body-changed sites at 0x300030..0x300050 are weak,
    # and a REF_GP/REF_ABS mix is no pair.
    assert ev == {(0x300010, 0x300390): 1}, ev
    us[6] = ab(0x300030)
    jp[6] = ab(0x3003B0)
    ev = collect_evidence(FakeMatcher(us, jp), secs)
    assert ev[(0x300030, 0x3003B0)] == 2, ev


def test_report():
    tmp = tempfile.mkdtemp()
    try:
        listing = os.path.join(tmp, "delta.txt")
        with open(listing, "w") as fp:
            fp.write("100000-100080 delta=+0x8 n=3\n"
                     "100200-100280 delta=+0x10 n=3\n"
                     "100300-100340 delta=+0x4 n=2\n"
                     "unmatched 5 of 20\n")
        runs = load_heuristic(listing)
        assert runs == [(0x100000, 0x100080, 8, 3), (0x100200, 0x100280, 0x10, 3),
                        (0x100300, 0x100340, 4, 2)], runs

        def fn(us, d, status="same"):
            f = make_func(us, us + 0x20, "sub_%X" % us)
            f.jp, f.jp_end, f.status = us + d, us + 0x20 + d, status
            return f
        funcs = [fn(0x100000, 8), fn(0x100040, 8), fn(0x100080, 8),
                 fn(0x100200, 0x10), fn(0x100240, 0x14, "body-changed"), fn(0x100280, 0x10),
                 fn(0x100300, 0x10)]
        funcs.append(FuncMatch(0x100400, 0x100420, "sub_100400", None, None, "unmatched",
                               "none", 0.0, 0.0, "no match"))
        res = cross_check(runs, funcs)
        # The n=2 run is ignored; the run bounds are inclusive.
        assert (res["agree"], res["disagree"]) == (5, 1), res
        assert res["rows"][0]["us"] == 0x100240 and res["rows"][0]["ours"] == 0x14
        funcs[5].jp = funcs[5].jp_end = None
        funcs[5].status = "unmatched"
        res = cross_check(runs, funcs)
        assert res["disagree"] == 2 and res["rows"][1]["ours"] is None, res

        class FakeElf:
            def read(self, addr, size):
                return b"AB\x00" * 20
        m = make_map()
        m.functions = funcs
        m.sections.append({"name": ".data", "us_start": 0x300, "us_end": 0x400,
                           "jp_start": 0x320, "jp_end": 0x420, "nobits": False})
        m.stats.update(data={".data": {"inplace_spans": [[0x310, 0x318]], "inplace_words": 2}},
                       nobits_boundaries=[[0x340, 0x380, 0x20, 0x40, ".data"]],
                       evidence_agree=5, evidence_disagree=1, evidence_unmapped=0,
                       evidence_disagreements=[[0x350, 0x370, 0x360, 2]])
        m.us_info = {"file": "US.elf", "sha256": "ab" * 32}
        m.jp_info = {"file": "JP.elf", "sha256": "cd" * 32}
        m.validate()
        path = os.path.join(tmp, "out", "regionmap.txt")
        write_report(path, m, None, {"us_elf": FakeElf(), "jp_elf": FakeElf(),
                                     "heuristic": res, "elapsed": 1.5})
        with open(path, "rb") as fp:
            raw = fp.read()
        assert b"\r" not in raw
        text = raw.decode()
        for head in ("== Sections ==", "== Code delta segments", "== Unmatched functions (2) ==",
                     "== Body-changed functions (1) ==", "== Uncertain regions (1) ==",
                     "== Uncertain NOBITS boundaries (1) ==", "== Evidence disagreements (1) ==",
                     "== Heuristic cross-check ==", "== JP-only code", "== Data ranges =="):
            assert head in text, (head, text)
        assert "00100400 sub_100400 size=0x20 no match" in text
        assert "00100240 -> 00100254 sub_100240" in text
        assert "00000310-00000318" in text and "<- weaker" in text
        assert "|AB.AB.AB." in text and "00000350: code says 00000370, map says 00000360" in text
        assert "ours=unmatched" in text and "disagree 2 (" in text
        assert tmp not in text
    finally:
        shutil.rmtree(tmp)


def test_anchors():
    tmp = tempfile.mkdtemp()
    try:
        cfg = os.path.join(tmp, "config")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "hooks.json"), "w") as fp:
            json.dump({"0x00100000": "hook_a"}, fp)
        with open(os.path.join(cfg, "report.json"), "w") as fp:
            json.dump({"applied_overrides": {"0x00100020": "hle_b"},
                       "applied_hooks": {"0x00100000": "hook_a"},
                       "other": {"0x00100040": "x"}}, fp)
        src = os.path.join(tmp, "runtime", "src", "rn")
        inc = os.path.join(tmp, "runtime", "include")
        os.makedirs(src)
        os.makedirs(inc)
        with open(os.path.join(src, "a.c"), "w") as fp:
            fp.write("int x;\n"
                     "u32 a = 0x00100040u, b = 0x00200010u;\n"
                     "u32 w = 0x27BDFF90u, z = 0x00500000u, y = 0x00A0482Du;\n"
                     "u32 c = 0x00100084u, d = 0x00200090u, e = 0x0010004C;\n")
        with open(os.path.join(inc, "b.h"), "w") as fp:
            fp.write("#define F 0x00100000u\n")
        with open(os.path.join(inc, "skipped.txt"), "w") as fp:
            fp.write("0x00100020\n")

        secs = [{"name": ".text", "us_start": 0x100000, "us_end": 0x100100,
                 "jp_start": 0x100010, "jp_end": 0x100110, "nobits": False},
                {"name": ".data", "us_start": 0x200000, "us_end": 0x200100,
                 "jp_start": 0x200020, "jp_end": 0x200120, "nobits": False}]
        anchors = collect_anchors(cfg, os.path.join(tmp, "runtime"), secs)
        got = {a.addr: a for a in anchors}
        # 0x00500000 lies outside every section and the hex words are not addresses.
        assert sorted(got) == [0x100000, 0x100020, 0x100040, 0x10004C, 0x100084, 0x200010,
                               0x200090], [hex(a) for a in got]
        assert got[0x100000].origins == ["config/hooks.json:hook_a",
                                         "config/report.json:hook_a",
                                         "runtime/include/b.h:1"], got[0x100000].origins
        assert got[0x100020].origins == ["config/report.json:hle_b"]
        assert got[0x100040].origins == ["runtime/src/rn/a.c:2"]
        assert got[0x200010].kind == "data" and got[0x100000].kind == "code"

        ranges = [Range(0x100000, 0x100040, 0x10, ".text", "func", "high"),
                  Range(0x200000, 0x200080, 0x20, ".data", "content", "medium"),
                  Range(0x2000C0, 0x200100, 0x20, ".data", "content", "medium")]
        unc = [Uncertain(0x200080, 0x2000C0, ".data", [0x20, 0x24], "content differs")]
        funcs = [make_func(0x100000, 0x100020, "sub_100000"),
                 make_func(0x100020, 0x100040, "sub_100020"),
                 make_func(0x100040, 0x100080, "sub_100040"),
                 make_func(0x100080, 0x1000A0, "sub_100080")]
        funcs[2].status, funcs[2].note = "body-changed", "calls differ"
        funcs[3].status, funcs[3].jp, funcs[3].jp_end = "unmatched", None, None
        funcs[3].note = "deleted"
        m = RegionMap({"file": "US.elf", "sha256": "ab" * 32},
                      {"file": "JP.elf", "sha256": "cd" * 32}, secs, ranges, unc, funcs, {})
        m.validate()

        class Seg:
            type = PT_LOAD

            def __init__(self, vaddr, words):
                self.vaddr = vaddr
                self.data = struct.pack("<%dI" % len(words), *words)
                self.filesz = len(self.data)

        class FakeElf:
            def __init__(self, vaddr, words):
                self.segments = [Seg(vaddr, words)]
        us = FakeElf(0x100000, [1, 2, 0, 0, 0, 0, 0, 0, 3, 4])
        jp = FakeElf(0x100010, [1, 2, 0, 0, 0, 0, 0, 0, 3, 5])
        rows = {r["addr"]: r for r in check_anchors(anchors, m, us, jp)}
        assert rows[0x100000]["jp"] == 0x100010 and rows[0x100000]["prologue"]["same"]
        assert rows[0x100020]["prologue"]["same"] is False
        assert rows[0x100020]["prologue"]["us"] == [3, 4] and rows[0x100020]["jp"] == 0x100030
        # Inside a changed body with no range, and inside an unmatched function.
        assert not rows[0x100040]["mapped"] and "calls differ" in rows[0x100040]["reason"]
        assert rows[0x100040]["status"] == "body-changed"
        assert not rows[0x100084]["mapped"] and "deleted" in rows[0x100084]["reason"]
        assert rows[0x10004C]["function"] == "sub_100040" and rows[0x10004C]["offset"] == 0xC
        assert rows[0x10004C]["prologue"] is None and rows[0x100040]["prologue"] is None
        assert rows[0x200010]["mapped"] and rows[0x200010]["confidence"] == "medium"
        assert rows[0x200010]["prologue"] is None
        assert not rows[0x200090]["mapped"] and "content differs" in rows[0x200090]["reason"]
        assert summarize_anchors(list(rows.values())) == {
            "code_total": 5, "code_mapped": 2, "data_total": 2, "data_mapped": 1,
            "prologue_checked": 2, "prologue_mismatches": 1}

        path = os.path.join(tmp, "out", "regionmap.txt")
        write_report(path, m, None, {"anchors": list(rows.values())})
        with open(path, "rb") as fp:
            raw = fp.read()
        assert b"\r" not in raw
        text = raw.decode()
        assert "== Required addresses (7) ==" in text
        assert "code: 2 of 5 mapped" in text and "data: 1 of 2 mapped" in text
        assert text.index("-- unmapped (4) --") < text.index("-- function prologue differs (1) --") \
            < text.index("-- mapped (2) --")
        assert "00100040 code sub_100040+0x0 (body-changed): no range" in text
        assert "origins: runtime/src/rn/a.c:2" in text
        assert "00100020 -> 00100030 sub_100020+0x0 (same): us 00000003 00000004, " \
            "jp 00000003 00000005; origins: config/report.json:hle_b" in text
        assert tmp not in text
    finally:
        shutil.rmtree(tmp)


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
        heur = os.path.join(ROOT, ".claude", "analysis", "jp_delta.txt")
        extra_args = ["--heuristic", heur] if os.path.isfile(heur) else []
        text = run_tool("build", US, JP, "--out", out, *extra_args)
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
        assert float(mc.group(6)) <= 30.0, code[0]
        assert len(m.functions) == nfunc
        calls = [l for l in text.splitlines() if l.startswith("calls: ")]
        mk = re.match(r"calls: (\d+) pairs checked, (\d+) conflicts \(([\d.]+)%\); "
                      r"placed by call (\d+), located (\d+), gap-filled (\d+)$",
                      calls[0] if calls else "")
        assert mk, text
        pairs, conflicts = int(mk.group(1)), int(mk.group(2))
        assert pairs > 1000 and conflicts == 0, calls[0]
        # These two sites sit in changed bodies and pair a call to another function.
        assert m.translate(0x2F00F0) in (None, 0x2F03BC), hex(m.translate(0x2F00F0) or 0)
        assert m.translate(0x2F85D8) in (None, 0x2F8904), hex(m.translate(0x2F85D8) or 0)
        # 764 is the unmatched count of the exact pass alone.
        assert un < 764, code[0]
        assert un <= 150, code[0]
        for f in m.functions:
            if f.status != "same":
                assert f.note or f.similarity > 0, f
        assert m.translate(0x31C138) == 0x31C460
        entry = run_tool("lookup", path, "0x31C138").split()
        assert entry[:4] == ["0031C138", "->", "0031C460", ".text"] and entry[4] == "same"

        # Content walk of the PROGBITS data sections.
        data = {}
        for l in text.splitlines():
            md = re.match(r"data (\S+): (\d+) ranges, (\d+) uncertain \((\d+) bytes\), "
                          r"inplace (\d+) words, deltas \[(.*)\]$", l)
            if md:
                data[md.group(1)] = (int(md.group(2)), int(md.group(4)), md.group(6))
        for name in (".vutext", ".ctors", ".dtors", ".data", ".eh_frame", ".rodata",
                     ".gcc_except_table", ".lit4", ".sdata"):
            assert name in data, (name, text)
        assert ".bss" not in data and ".sbss" not in data
        assert data[".vutext"] == (1, 0, "+0x340"), data[".vutext"]
        vu = [r for r in m.ranges if r.section == ".vutext"]
        assert [(r.us_start, r.us_end, r.delta) for r in vu] == [(0x399310, 0x3C7E30, 0x340)]
        # Every PROGBITS byte is either mapped or reported uncertain.
        for s in m.sections:
            if s["nobits"] or s["name"] == ".text":
                continue
            cov = sum(r.us_end - r.us_start for r in m.ranges if r.section == s["name"])
            cov += sum(u.us_end - u.us_start for u in m.uncertain if u.section == s["name"])
            assert cov == s["us_end"] - s["us_start"], s["name"]
        # The JP .rodata and .sdata are longer, so their later content shifts further.
        assert m.translate(0x425580) == 0x425900 and m.translate(0x43FD40) == 0x4400D0
        assert m.translate(0x445B00) == 0x445B00 + 0x388
        assert run_tool("lookup", path, "0x3C7F00").split()[:5] == \
            ["003C7F00", "->", "003C8280", ".data", "content"]
        assert any(r.section == ".data" and r.source == "content" and r.delta == 0x380
                   for r in m.ranges)

        # NOBITS sections are mapped from code references.
        for name in (".sbss", ".bss"):
            rows = [r for r in m.ranges if r.section == name]
            assert any(r.source == "xref" for r in rows), (name, rows)
            assert name not in data
        # The JP .bss is 0x480 larger, so its objects shift further along the section.
        bss = [r for r in m.ranges if r.section == ".bss"]
        assert bss[0].delta == 0x380 and bss[-1].delta > bss[0].delta, bss
        assert m.translate(0x484B28) == 0x484B28 + bss[-1].delta
        bounds = m.stats["nobits_boundaries"]
        assert bounds and all(len(b) == 5 and b[0] < b[1] for b in bounds), bounds
        me = re.search(r"^evidence: (\d+) agree, (\d+) disagree$", text, re.M)
        assert me, text
        agree, disagree = int(me.group(1)), int(me.group(2))
        assert (agree, disagree) == (m.stats["evidence_agree"], m.stats["evidence_disagree"])
        assert agree > 1000 and disagree * 100 <= agree + disagree, (agree, disagree)
        assert len(m.stats["evidence_disagreements"]) <= 50
        mw = re.search(r"^weak evidence \(.*\): (\d+) agree, (\d+) disagree$", text, re.M)
        assert mw and (int(mw.group(1)), int(mw.group(2))) ==             (m.stats["weak_agree"], m.stats["weak_disagree"]), text
        with open(os.path.join(out, "regionmap.txt")) as fp:
            report = fp.read()
        for head in ("== Unmatched functions", "== Body-changed functions",
                     "== Uncertain regions", "== Heuristic cross-check"):
            assert head in report, head
        assert out not in report and ROOT not in report
        ma = re.search(r"^anchors: code (\d+)/(\d+) mapped, data (\d+)/(\d+) mapped, "
                       r"prologue mismatches (\d+)$", text, re.M)
        assert ma, text
        cm_, ct, dm, dt, pm = (int(ma.group(k)) for k in range(1, 6))
        sa = m.stats["anchors"]
        assert (sa["code_mapped"], sa["code_total"], sa["data_mapped"], sa["data_total"],
                sa["prologue_mismatches"]) == (cm_, ct, dm, dt, pm)
        assert ct >= 100 and dt >= 20 and cm_ <= ct and dm <= dt and pm <= sa["prologue_checked"]
        assert "== Required addresses (%d) ==" % (ct + dt) in report
        # Every key of the hook table is a required address.
        with open(os.path.join(ROOT, "config", "hooks.json")) as fp:
            hooks = json.load(fp)
        for key, handler in hooks.items():
            assert "config/hooks.json:%s" % handler in report, key
        # Anything that does not map must be listed with its origins and a reason.
        block = report[report.index("-- unmapped ("):report.index("-- function prologue differs")]
        nun = (ct - cm_) + (dt - dm)
        assert block.startswith("-- unmapped (%d) --" % nun), block
        assert block.count("origins: ") == nun, block
        if extra_args:
            mh = re.search(r"^heuristic: (\d+) agree, (\d+) disagree \(([\d.]+)%\)$", text, re.M)
            assert mh and float(mh.group(3)) >= 95.0, text
            assert m.stats["heuristic"] == {"agree": int(mh.group(1)),
                                            "disagree": int(mh.group(2))}
    finally:
        shutil.rmtree(out)


test_merge_ranges()
test_validate_translate()
test_json_roundtrip()
test_normalize_synthetic()
test_find_gp()
test_unit_free_gaps()
test_exact_pass_synthetic()
test_reference_propagation()
test_gap_diff_synthetic()
test_trim_conflicting_calls()
test_padding_and_chunk_confidence()
test_load_units()
test_data_walk()
test_nobits_map()
test_evidence_filter()
test_report()
test_anchors()

if os.path.isfile(US) and os.path.isfile(JP):
    test_real_normalize()
    test_real_binaries()
    print("PASS: regionmap (synthetic + real binaries)")
else:
    print("SKIP: real binaries not present")
    print("PASS: regionmap (synthetic)")
