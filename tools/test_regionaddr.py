import contextlib
import io
import os
import shutil
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

from regionaddr import Entry, ListError, lint, parse_list
from regionaddr.__main__ import main

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


test_parser()
test_lint_tree()
test_real_tree()
print("PASS: regionaddr")
