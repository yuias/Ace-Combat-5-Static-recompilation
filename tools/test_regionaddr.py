import contextlib
import io
import os
import shutil
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

from types import SimpleNamespace

from regionaddr import Entry, ListError, gen, lint, parse_list
from regionaddr.__main__ import main
from regionconfig.core import Translator
from regionmap import FuncMatch, Range, RegionMap

US_ELF = os.path.join(ROOT, "tmp", "us", "SLUS_208.51")
JP_ELF = os.path.join(ROOT, "tmp", "jp", "SLPS_254.18")
MAP = os.path.join(ROOT, "tmp", "regionmap", "regionmap.json")

GOOD = """\
/* header comment
   spanning two lines */

PS2_ADDR(RN_A,        0x00320098u, FUNC, 4)
/* single-line comment */
PS2_ADDR(RN_A_END,    0x003200D4u, END,  0)
  PS2_ADDR( RN_RET_1 , 0x0012BF1Cu , RET , 0 )
PS2_ADDR(AC5_PTR,     0x004459A8u, DATA, 1)
"""


def raises_at(text, line, what):
    try:
        parse_list(text)
    except ListError as e:
        assert str(e).startswith("line %d:" % line), (what, str(e))
        return str(e)
    raise AssertionError("no error for " + what)


def test_parser():
    got = parse_list(GOOD)
    assert got == [Entry("RN_A", 0x320098, "FUNC", 4, 4),
                   Entry("RN_A_END", 0x3200D4, "END", 0, 6),
                   Entry("RN_RET_1", 0x12BF1C, "RET", 0, 7),
                   Entry("AC5_PTR", 0x4459A8, "DATA", 1, 8)], got
    assert parse_list("") == [] and parse_list("/* only a comment */\n\n") == []
    # CRLF input still parses
    assert len(parse_list(GOOD.replace("\n", "\r\n"))) == 4

    raises_at(GOOD + "int x;\n", 9, "stray code")
    raises_at("PS2_ADDR(A, 0x00100000u, FUNC, 4)\nPS2_ADDR(B, 0x00100004u, NOPE, 4)\n", 2,
              "unknown kind")
    raises_at("PS2_ADDR(A, 0x00100000u, FUNC, 9)\n", 1, "too many words")
    raises_at("PS2_ADDR(A, 0x00100000u, FUNC, 4) // tail\n", 1, "trailing text")
    raises_at("\n\nPS2_ADDR(A, 0x100000u, FUNC, 4)\n", 3, "short value")
    raises_at("PS2_ADDR(A, 0x0100000u, FUNC, 4)\n", 1, "seven digits")
    raises_at("PS2_ADDR(A, 0x000100000u, FUNC, 4)\n", 1, "nine digits")
    raises_at("PS2_ADDR(A, 0x00100000, FUNC, 4)\n", 1, "missing u suffix")
    msg = raises_at(GOOD + "PS2_ADDR(RN_A, 0x00100000u, CODE, 0)\n", 9, "duplicate name")
    assert "RN_A" in msg and "line 4" in msg, msg
    raises_at("PS2_ADDR(A, 0x00100000u, FUNC, 4)\nPS2_ADDR(B, 0x00100000u, FUNC, 4)\n", 2,
              "same address and kind")
    # the same address under another kind is allowed (function start / previous end)
    assert len(parse_list("PS2_ADDR(A, 0x00100000u, FUNC, 4)\n"
                          "PS2_ADDR(A_PREV_END, 0x00100000u, END, 0)\n")) == 2
    raises_at("/* open\nPS2_ADDR(A, 0x00100000u, FUNC, 4)\n", 1, "unterminated comment")
    raises_at("/* a */ PS2_ADDR(A, 0x00100000u, FUNC, 4)\n", 1, "entry after comment")


class Tree:
    """Temporary repo layout with runtime/src and runtime/include."""

    def __init__(self, list_text):
        self.root = tempfile.mkdtemp(prefix="regionaddr-")
        self.put("runtime/include/ps2_addr_list.h", list_text)

    def put(self, rel, text):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="\n") as f:
            f.write(text)

    def lint(self, strict=False):
        lines = []
        code = lint.run(self.root, strict=strict, log=lines.append)
        return code, lines

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def test_lint_tree():
    t = Tree(GOOD)
    try:
        # allowed: (file, value) is in ALLOW; the same value elsewhere is not
        t.put("runtime/src/ps2_core.c", "#define PS2_TEXT_LO 0x00100000u\n")
        # pending: a listed address, and the exclusive end next to a RET entry
        t.put("runtime/src/rn/rn_x.c",
              "u32 a = 0x00320098u;\nu32 b[2] = { 0x0012BF1Cu, 0x0012BF1Du };\n")
        # outside the range, short literals and non-hex tokens are ignored
        t.put("runtime/src/ps2_misc.c",
              "u32 a = 0x80000592u; u32 b = 0x0004FFFFu; u32 c = 0x500000u; u32 d = 0xFFFFu;\n"
              "u32 e = 0x1234567890; u32 f = 10000000;\n")
        t.put("runtime/include/ps2_addr_jp.inc", "PS2_ADDR_JP(RN_A, 0x00320098u)\n")
        code, lines = t.lint()
        assert code == 0, lines
        assert lines[-1] == "regionaddr lint: 1 allowed, 3 pending migration, 0 unknown", lines
        assert "runtime/src/rn/rn_x.c:1 0x00320098 pending migration" in lines, lines
        assert "runtime/src/rn/rn_x.c:2 0x0012BF1D pending migration" in lines, lines
        assert lint.run(t.root, strict=True, log=lambda s: None) == 1
        code, _ = t.lint(strict=True)
        assert code == 1

        # unknown: not listed and not allowed
        t.put("runtime/src/ps2_misc.c", "u32 a = 0x00123456u;\n")
        code, lines = t.lint()
        assert code == 1, lines
        assert "runtime/src/ps2_misc.c:1 0x00123456 unknown" in lines, lines
        assert lines[-1] == "regionaddr lint: 1 allowed, 3 pending migration, 1 unknown", lines
        code, _ = t.lint(strict=True)
        assert code == 1

        # migrated: only the allowed literal remains
        t.put("runtime/src/ps2_misc.c", "int x;\n")
        t.put("runtime/src/rn/rn_x.c", "u32 a = PS2_A(RN_A);\n")
        for strict in (False, True):
            code, lines = t.lint(strict=strict)
            assert code == 0 and lines == ["regionaddr lint: 1 allowed, 0 pending migration, "
                                           "0 unknown"], lines

        # the list file itself is not scanned
        t.put("runtime/include/ps2_addr_list.h", GOOD + "/* 0x00123456u */\n")
        assert t.lint()[0] == 0
        # a broken list is an error, not a clean run
        t.put("runtime/include/ps2_addr_list.h", "junk\n")
        try:
            t.lint()
            raise AssertionError("broken list accepted")
        except ListError:
            pass
    finally:
        t.close()


def test_real_tree():
    path = os.path.join(ROOT, *lint.LIST_REL.split("/"))
    with open(path, "rb") as f:
        raw = f.read()
    assert b"\r" not in raw, "list file has CR"
    entries = parse_list(raw.decode("utf-8"))
    assert entries, "empty list"
    for e in entries:
        assert 0x100000 <= e.us < 0x500000, e
        assert e.kind != "END" or e.nwords == 0, e
    for e in entries:
        if e.name.endswith("_END"):
            assert e.kind == "END", e

    hits, _ = lint.scan(ROOT)
    states = {(rel, v): s for rel, _, v, s in hits}
    for rel, value, reason in lint.ALLOW:
        assert reason and (rel, value) in states, (rel, hex(value), "stale ALLOW item")
    assert not [h for h in hits if h[3] == lint.UNKNOWN], [h for h in hits if h[3] == "unknown"]
    assert lint.run(ROOT, log=lambda s: None) == 0
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(["lint"]) == 0


def test_real_tree_strict():
    # every listed address in the runtime sources goes through PS2_A
    lines = []
    assert lint.run(ROOT, strict=True, log=lines.append) == 0, lines
    assert lines[-1].endswith("0 pending migration, 0 unknown"), lines
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(["lint", "--strict"]) == 0


class FakeElf:
    def __init__(self, sections, words):
        self.sections = [SimpleNamespace(name=n, addr=a, size=z, is_alloc=True)
                         for n, a, z in sections]
        self.mem = words

    def word(self, addr):
        return self.mem.get(addr, 0)


def gen_fixture(jp_words=None, us_words=None):
    """US .text 100000-100400 maps with +10 / +20 (medium) / +30; .data +40."""
    ranges = [Range(0x100000, 0x100200, 0x10, ".text", "func", "high"),
              Range(0x100200, 0x100300, 0x20, ".text", "func", "medium"),
              Range(0x100300, 0x1003F0, 0x30, ".text", "func", "high"),
              Range(0x200000, 0x200100, 0x40, ".data", "section", "high")]
    funcs = [FuncMatch(0x100000, 0x100040, "sub_a", 0x100010, 0x100050, "same", "exact", 1.0, 1.0),
             FuncMatch(0x100040, 0x100080, "sub_b", 0x100050, 0x100090, "same", "exact", 1.0, 1.0),
             FuncMatch(0x100200, 0x100240, "sub_m", 0x100220, 0x100260, "body-changed", "diff",
                       0.9, 0.9),
             FuncMatch(0x100300, 0x100340, "sub_c", 0x100330, 0x100370, "same", "exact", 1.0, 1.0)]
    secs = [{"name": ".text", "us_start": 0x100000, "us_end": 0x100400,
             "jp_start": 0x100000, "jp_end": 0x100440, "nobits": False}]
    rmap = RegionMap({"file": "US", "sha256": "aa"}, {"file": "JP", "sha256": "bb"},
                     secs, ranges, [], funcs, {})
    us_mem = {0x100000: 0x27BDFFF0, 0x100004: 0xFFBF0000, 0x100040: 0x03E00008,
              0x100200: 0x11111111, 0x100300: 0x22222222, 0x200000: 0xCAFE}
    jp_mem = {0x100010: 0x27BDFFF0, 0x100014: 0xFFBF0000, 0x100050: 0x03E00008,
              0x100220: 0x11111111, 0x100330: 0x22222222, 0x200040: 0xCAFE}
    jp_mem.update(jp_words or {})
    us_mem.update(us_words or {})
    us =FakeElf([(".text", 0x100000, 0x400), (".data", 0x200000, 0x100)], us_mem)
    jp = FakeElf([(".text", 0x100000, 0x440), (".data", 0x200040, 0x100)], jp_mem)
    return Translator(rmap, us, jp)


LIST = """\
PS2_ADDR(F_A,      0x00100000u, FUNC, 2)
PS2_ADDR(F_A_END,  0x00100040u, END,  0)
PS2_ADDR(F_B,      0x00100040u, FUNC, 1)
PS2_ADDR(F_M,      0x00100200u, FUNC, 1)
PS2_ADDR(C_X,      0x00100300u, CODE, 1)
PS2_ADDR(D_X,      0x00200000u, DATA, 1)
PS2_ADDR(T_END,    0x00100400u, END,  0)
PS2_ADDR(M_END,    0x00100240u, END,  0)
"""

M_VERDICT = {"0x00100200": {"name": "F_M", "verdict": "changed-verified", "jp": "0x00100220",
                            "note": "n"}}
M_END_VERDICT = {"name": "M_END", "verdict": "relocated-only", "note": "n"}
BOTH = dict(M_VERDICT, **{"0x00100240": M_END_VERDICT})


def gen_build(list_text=LIST, verdicts=None, allow_pending=True, ida=None, **kw):
    v = {"addresses": dict(verdicts or {}), "config": {}, "values": {}}
    return gen.build(parse_list(list_text), gen_fixture(**kw), v, ida, allow_pending)


def jp_rows(res):
    return {l.split("(", 1)[1].split(",", 1)[0]: l for l in res.jp_text.split("\n")
            if l.startswith("PS2_ADDR_JP(")}


def has(lines, text):
    return any(text in l for l in lines)


def test_gen_resolution():
    res = gen_build(verdicts=BOTH)
    assert res.errors == [], res.errors
    assert res.jp_text.split("\n")[0] == gen.HEADER
    assert "PENDING" not in res.jp_text and not res.jp_text.split("\n")[1].startswith("#")
    rows = jp_rows(res)
    assert rows["F_A"] == "PS2_ADDR_JP(F_A, 0x00100010u)  /* FUNC map */", rows["F_A"]
    assert rows["F_M"] == "PS2_ADDR_JP(F_M, 0x00100220u)  /* FUNC verdict changed-verified */"
    # An ordinary END goes through Translator.end; a section end maps to the JP
    # section end, which the range table cannot see (JP has extra code at the end).
    assert "0x00100050u)  /* END map */" in rows["F_A_END"], rows["F_A_END"]
    assert "T_END, 0x00100440u)  /* END section end */" in rows["T_END"], rows["T_END"]
    assert "M_END, 0x00100260u" in rows["M_END"], rows["M_END"]
    assert rows["D_X"].endswith("0x00200040u)  /* DATA map */"), rows["D_X"]
    assert list(rows) == [e.name for e in parse_list(LIST)], "list order"
    # code words only: the DATA word is checked but has no row
    assert res.words_text.split("\n")[1:-1] == [
        "PS2_WORDS_US(F_A, 2, 0x27BDFFF0u, 0xFFBF0000u)",
        "PS2_WORDS_JP(F_A, 2, 0x27BDFFF0u, 0xFFBF0000u)",
        "PS2_WORDS_US(F_B, 1, 0x03E00008u)", "PS2_WORDS_JP(F_B, 1, 0x03E00008u)",
        "PS2_WORDS_US(F_M, 1, 0x11111111u)", "PS2_WORDS_JP(F_M, 1, 0x11111111u)",
        "PS2_WORDS_US(C_X, 1, 0x22222222u)", "PS2_WORDS_JP(C_X, 1, 0x22222222u)"], res.words_text
    assert res.summary() == ("regionaddr: 8 entries, 6 by map, 2 by verdict, 0 pending, "
                             "5 word checks (0 differ)"), res.summary()


def test_gen_verdict_gate():
    # a medium range and a body-changed function need a verdict
    res = gen_build(allow_pending=False)
    assert has(res.errors, "F_M") and has(res.errors, "range confidence medium"), res.errors
    assert has(res.errors, "is body-changed"), res.errors
    res = gen_build()
    assert res.errors == [] and len(res.needs) == 2, (res.errors, res.needs)  # F_M, M_END
    lines = res.jp_text.split("\n")
    assert lines[1] == "#define PS2_ADDR_JP_PENDING 2", lines[:3]
    assert jp_rows(res)["F_M"].startswith("PS2_ADDR_JP(F_M, 0x00000000u)  /* PENDING "), res.jp_text
    assert "PS2_WORDS_US(F_M" in res.words_text and "PS2_WORDS_JP(F_M" not in res.words_text
    assert "0 by verdict, 2 pending" in res.summary(), res.summary()
    one = gen_build("PS2_ADDR(F_M, 0x00100200u, FUNC, 1)\n")
    assert one.jp_text.split("\n")[1] == "#define PS2_ADDR_JP_PENDING 1"

    # the pinned jp must equal the computed value
    assert gen_build(verdicts=BOTH).errors == []
    stale = dict(BOTH, **{"0x00100200": dict(M_VERDICT["0x00100200"], jp="0x00100230")})
    assert has(gen_build(verdicts=stale).errors, "stale verdict"), "stale jp accepted"
    # unused: not needed, wrong name, no entry at the address
    unused = dict(BOTH, **{"0x00100000": {"name": "F_A", "verdict": "same-code", "note": "n"}})
    assert has(gen_build(verdicts=unused).errors, "F_A (0x00100000, FUNC): verdict same-code is not needed")
    wrong = dict(BOTH, **{"0x00100200": dict(M_VERDICT["0x00100200"], name="OTHER")})
    errs = gen_build(verdicts=wrong, allow_pending=False).errors
    assert has(errs, "does not match list entry F_M") and has(errs, "needs a verdict"), errs
    ghost = dict(BOTH, **{"0x00100010": {"name": "X", "verdict": "same-code", "note": "n"}})
    assert has(gen_build(verdicts=ghost).errors, "no list entry")
    bad = dict(BOTH, **{"0x00100200": {"name": "F_M", "verdict": "nope"}})
    assert has(gen_build(verdicts=bad).errors, "unknown verdict")
    # broken fails even with --allow-pending
    broken = dict(BOTH, **{"0x00100200": dict(M_VERDICT["0x00100200"], verdict="broken",
                                              note="why")})
    assert has(gen_build(verdicts=broken, allow_pending=True).errors, "verdict broken: why")
    # outside every mapped range: pending; only manual-jp can supply the value
    nomap = "PS2_ADDR(Q, 0x001003F8u, CODE, 0)\n"
    res = gen_build(nomap)
    assert res.pending == 1 and not res.errors and has(res.needs, "no JP value"), res.needs
    q = {"0x001003F8": {"name": "Q", "verdict": "changed-verified", "note": "n"}}
    assert has(gen_build(nomap, verdicts=q).errors, "use manual-jp")
    q["0x001003F8"] = {"name": "Q", "verdict": "manual-jp", "jp": "0x00100600", "note": "n"}
    res = gen_build(nomap, verdicts=q)
    assert res.errors == [] and res.by_verdict == 1, res.errors
    assert "0x00100600u)  /* CODE verdict manual-jp */" in res.jp_text, res.jp_text


def test_gen_words_and_selfchecks():
    # a differing code word needs a verdict
    res = gen_build(verdicts=BOTH, jp_words={0x100014: 0xFFBF0004})
    assert has(res.needs, "words differ") and "F_A" in res.needs[0], res.needs
    assert res.word_diffs == 1 and res.pending == 1, res.summary()
    ok = dict(BOTH, **{"0x00100000": {"name": "F_A", "verdict": "relocated-only", "note": "n"}})
    res = gen_build(verdicts=ok, jp_words={0x100014: 0xFFBF0004})
    assert res.errors == [] and res.by_verdict == 3, res.summary()
    assert "PS2_WORDS_JP(F_A, 2, 0x27BDFFF0u, 0xFFBF0004u)" in res.words_text
    # DATA words are compared, but no row is written
    res = gen_build(verdicts=BOTH, jp_words={0x200040: 0xBEEF})
    assert has(res.needs, "D_X") and has(res.needs, "words differ"), res.needs
    assert "D_X" not in res.words_text
    assert "D_X" not in gen_build(verdicts=BOTH).words_text
    # list self-checks cannot be waived
    assert has(gen_build("PS2_ADDR(Z, 0x00100004u, FUNC, 0)\n").errors, "function or chunk start")
    assert has(gen_build("PS2_ADDR(Z, 0x00200000u, CODE, 0)\n").errors, "not inside .text")
    assert has(gen_build("PS2_ADDR(Z, 0x00100000u, DATA, 0)\n").errors, "inside .text")
    # a FUNC that is an ida_db function in US must be one in JP
    assert gen_build(ida=({0x100000}, {0x100010}), verdicts=BOTH).errors == []
    res = gen_build(ida=({0x100000}, {0x100050}), verdicts=BOTH)
    assert has(res.needs, "not a function in the JP ida_db"), res.needs
    # an end that is not above its start needs a verdict
    tr = gen_fixture()
    tr.rmap.functions[0].jp = 0x100030  # the match places the start above the range's end
    res = gen.build(parse_list("PS2_ADDR(F_A, 0x00100000u, FUNC, 0)\n"
                               "PS2_ADDR(F_A_END, 0x00100008u, END, 0)\n"),
                    tr, {"addresses": {}, "config": {}, "values": {}}, None, True)
    assert has(res.needs, "F_A_END") and has(res.needs, "not above"), res.needs


JAL_US = 0x0C040010  # jal 0x00100040 (sub_b); its JP entry is 0x00100050
JAL_JP = 0x0C040014
JALR = 0x0040F809  # jalr ra, v0
RET_A = "PS2_ADDR(R_A, 0x00100010u, RET, 0)\n"  # same function, high range; JP 0x00100020
RET_M = "PS2_ADDR(R_M, 0x00100210u, RET, 0)\n"  # body-changed function, medium range; map 0x00100230
RET_M_VERDICT = {"0x00100210": {"name": "R_M", "verdict": "changed-verified",
                                "jp": "0x00100230", "note": "n"}}


def test_gen_ret():
    # the call before the return address is proved from the JP side
    res = gen_build(RET_A, us_words={0x100008: JAL_US}, jp_words={0x100018: JAL_JP})
    assert res.errors == [] and res.pending == 0, (res.errors, res.needs)
    assert jp_rows(res)["R_A"] == "PS2_ADDR_JP(R_A, 0x00100020u)  /* RET map */", res.jp_text
    # a call to some other function does not prove it
    res = gen_build(RET_A, us_words={0x100008: JAL_US}, jp_words={0x100018: JAL_US})
    assert res.pending == 1 and has(res.needs, "RET not proved by its call"), res.needs
    # the US word before the return address must be a call
    res = gen_build(RET_A)
    assert has(res.errors, "is not jal/jalr") and res.pending == 0, res.errors
    res = gen_build("PS2_ADDR(R_B, 0x00200010u, RET, 0)\n", us_words={0x200008: JAL_US})
    assert has(res.errors, "not inside .text"), res.errors

    # body-changed function, medium range: the map value fails the check, but exactly one
    # JP call to the translated target exists, so the pairing needs no verdict
    us = {0x100208: JAL_US}
    res = gen_build(RET_M, us_words=us, jp_words={0x100238: JAL_JP})
    assert res.errors == [] and res.pending == 0, (res.errors, res.needs)
    assert jp_rows(res)["R_M"] == "PS2_ADDR_JP(R_M, 0x00100240u)  /* RET jal pairing */", res.jp_text
    assert res.by_map == 1, res.summary()
    # the map value passes the check at JP-8: no pairing search
    res = gen_build(RET_M, us_words=us, jp_words={0x100228: JAL_JP})
    assert jp_rows(res)["R_M"] == "PS2_ADDR_JP(R_M, 0x00100230u)  /* RET map */", res.jp_text
    # no call at all, and two calls: needs a verdict, which pins the map value
    res = gen_build(RET_M, us_words=us)
    assert res.pending == 1 and has(res.needs, "0 such call(s) in the JP function"), res.needs
    two = {0x100238: JAL_JP, 0x100250: JAL_JP}
    res = gen_build(RET_M, us_words=us, jp_words=two)
    assert res.pending == 1 and has(res.needs, "2 such call(s) in the JP function"), res.needs
    assert has(res.needs, "range confidence medium") and has(res.needs, "body-changed")
    res = gen_build(RET_M, verdicts=RET_M_VERDICT, us_words=us, jp_words=two)
    assert res.errors == [] and res.by_verdict == 1, (res.errors, res.needs)
    assert "0x00100230u)  /* RET verdict changed-verified */" in res.jp_text, res.jp_text
    # a twin call in the US function makes a single JP hit ambiguous
    res = gen_build(RET_M, us_words={**us, 0x100220: JAL_US}, jp_words={0x100238: JAL_JP})
    assert res.pending == 1 and has(res.needs, "2 in the US one"), res.needs

    # a verdict for a RET that resolves by itself is unused, manual-jp included
    for v in (dict(RET_M_VERDICT["0x00100210"]),
              {"name": "R_M", "verdict": "manual-jp", "jp": "0x00100244", "note": "n"}):
        res = gen_build(RET_M, verdicts={"0x00100210": v}, us_words=us,
                        jp_words={0x100238: JAL_JP})
        assert has(res.errors, "R_M (0x00100210, RET): verdict %s is not needed" % v["verdict"]), \
            res.errors
        assert "0x00100240u)  /* RET jal pairing */" in res.jp_text

    # a jalr cannot be checked: needs a verdict whatever the map says
    res = gen_build(RET_A, us_words={0x100008: JALR})
    assert res.pending == 1 and has(res.needs, "jalr call"), (res.errors, res.needs)
    v = {"0x00100010": {"name": "R_A", "verdict": "same-code", "jp": "0x00100020", "note": "n"}}
    res = gen_build(RET_A, verdicts=v, us_words={0x100008: JALR})
    assert res.errors == [] and res.by_verdict == 1, res.errors
    # words after the return address are still compared
    res = gen_build("PS2_ADDR(R_A, 0x00100010u, RET, 1)\n", us_words={0x100008: JAL_US},
                    jp_words={0x100018: JAL_JP, 0x100020: 7})
    assert has(res.needs, "words differ"), res.needs


def test_show():
    tr = gen_fixture(us_words={0x100008: JAL_US}, jp_words={0x100018: JAL_JP})
    lines = gen.show_lines(tr, 0x100010, before=2, after=1)
    assert lines[0].startswith("function: sub_a  US 00100000-00100040  JP 00100010-00100050  "
                               "status same"), lines
    assert lines[1].startswith("range: 00100000-00100200 delta +0x10") and "confidence high" in lines[1]
    assert "anchor: US 00100010  JP 00100020" in lines, lines
    rows = lines[-4:]
    assert len(rows) == 4, rows
    # the call row differs (relocated target) and shows both columns
    assert rows[0].startswith("*  00100008  0C040010 JAL 00100040") and \
        rows[0].endswith("| 00100018  0C040014 JAL 00100050"), rows[0]
    assert rows[1].startswith("   0010000C") and "NOP" in rows[1], rows
    assert rows[2].startswith(">  00100010") and "| 00100020" in rows[2], rows[2]
    # an unmapped anchor shows the US side only; --jp pairs by hand
    lines = gen.show_lines(tr, 0x1003F8, before=0, after=0)
    assert lines[0] == "function: none in the map" and lines[-1].endswith("| -"), lines
    lines = gen.show_lines(tr, 0x1003F8, 0, 0, jp=0x100018)
    assert has(lines, "JP anchor given by hand") and lines[-1].endswith("| 00100018  0C040014 JAL "
                                                                        "00100050"), lines
    out = []
    assert gen.run_show(tr, 0x100010, 1, 1, log=out.append) == 0 and len(out) == len(
        gen.show_lines(tr, 0x100010, 1, 1))


def test_gen_check():
    t = tempfile.mkdtemp(prefix="regionaddr-")
    try:
        jp_path, words_path = os.path.join(t, "jp.inc"), os.path.join(t, "w.inc")
        entries = parse_list(LIST)
        v = {"addresses": BOTH, "config": {}, "values": {}}
        v0 = {"addresses": {}, "config": {}, "values": {}}
        quiet = lambda s: None  # noqa: E731

        def run_check(verdicts=v):
            return gen.run_check(entries, gen_fixture(), verdicts, None, jp_path, words_path,
                                 quiet)

        def run_generate(verdicts=v, allow_pending=False):
            return gen.run_generate(entries, gen_fixture(), verdicts, None, jp_path, words_path,
                                    allow_pending=allow_pending, log=quiet)

        assert run_check() == 1, "missing files"
        assert run_generate() == 0
        with open(jp_path, "rb") as f:
            raw = f.read()
        assert b"\r" not in raw and raw.endswith(b"\n")
        assert run_check() == 0
        with open(jp_path, "wb") as f:
            f.write(raw.replace(b"0x00100010u", b"0x00100011u"))
        assert run_check() == 1, "edited byte"
        with open(jp_path, "wb") as f:
            f.write(raw)
        with open(words_path, "ab") as f:
            f.write(b" ")
        assert run_check() == 1, "words file differs"

        # pending: generate refuses without --allow-pending (and writes nothing);
        # check fails on a PENDING file even though it is current
        os.remove(jp_path)
        assert run_generate(v0) == 1 and not os.path.exists(jp_path)
        assert run_generate(v0, allow_pending=True) == 0
        with open(jp_path, "rb") as f:
            assert b"PENDING" in f.read()
        assert run_check(v0) == 1
    finally:
        shutil.rmtree(t, ignore_errors=True)


def test_gen_real():
    if not all(os.path.isfile(p) for p in (US_ELF, JP_ELF, MAP)):
        print("SKIP: regionaddr generator on the real binaries (inputs missing)")
        return
    t = tempfile.mkdtemp(prefix="regionaddr-")
    try:
        jp_out = os.path.join(t, "jp.inc")
        out = ["--jp-out", jp_out, "--words-out", os.path.join(t, "w.inc")]
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = main(["generate", "--allow-pending"] + out)
        assert code == 0, buf.getvalue()
        with open(os.path.join(ROOT, *lint.LIST_REL.split("/")), encoding="utf-8") as f:
            n = len(parse_list(f.read()))
        assert "regionaddr: %d entries," % n in buf.getvalue(), buf.getvalue()
        with open(jp_out, encoding="utf-8") as f:
            text = f.read()
        rows = [l for l in text.split("\n") if l.startswith("PS2_ADDR_JP(")]
        assert len(rows) == n
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(["check"] + out) == (1 if "PENDING" in text else 0)
        # JP has more game code than US, so the end of the used code sits a little
        # below the end of the JP .text instead of at a US-shaped offset
        code_end = int(next(r for r in rows if "AC5_CODE_END" in r).split("0x")[1][:8], 16)
        assert 0x399400 <= code_end <= 0x399650, hex(code_end)
    finally:
        shutil.rmtree(t, ignore_errors=True)


test_parser()
test_lint_tree()
test_real_tree()
test_real_tree_strict()
test_gen_resolution()
test_gen_verdict_gate()
test_gen_words_and_selfchecks()
test_gen_ret()
test_show()
test_gen_check()
test_gen_real()
print("PASS: regionaddr")
