"""Handler for ida_seeds.json: translated seed lists plus JP-only code seeds."""
import bisect
import json
import os
import struct
from typing import Callable, Dict, Iterable, List, Set, Tuple

from ps2recomp.r5900 import CAT_BRANCH, CAT_BRANCH_LIKELY, CAT_JUMP, CAT_JUMPR, decode

from .core import Translator, dump_json

FILE = "ida_seeds.json"
LISTS = ("xref", "ptr", "flow")
REDERIVED = "rederived"
_RA = 31
# Jump tables hold switch case labels, which the switch handler covers.
_SKIP_SECTIONS = (".rodata", ".gcc_except_table", ".eh_frame")
_PAD_WORDS = 7  # nops accepted between the previous function's return and a seed

Words = Callable[[int], int]


def _read(src_dir: str) -> bytes:
    path = os.path.join(src_dir, FILE)
    if not os.path.isfile(path):
        raise SystemExit("regionconfig: missing source file %s" % path.replace("\\", "/"))
    with open(path, "rb") as fp:
        return fp.read()


def jp_only_stretches(rmap) -> List[Tuple[int, int]]:
    """JP .text stretches that no .text range covers (no minimum size)."""
    text = next((s for s in rmap.sections if s["name"] == ".text"), None)
    if text is None:
        return []
    out = []
    cur = text["jp_start"]
    covered = sorted((r.us_start + r.delta, r.us_end + r.delta)
                     for r in rmap.ranges if r.section == ".text")
    for lo, hi in covered + [(text["jp_end"], text["jp_end"])]:
        if lo > cur:
            out.append((cur, lo))
        cur = max(cur, hi)
    return out


def merged_spans(tr: Translator) -> List[Tuple[int, int]]:
    """Every JP span of every mapped function, merged and sorted."""
    spans = sorted(s for f in tr.rmap.functions for s in tr.jp_spans(f.us))
    out: List[List[int]] = []
    for lo, hi in spans:
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(lo, hi) for lo, hi in out]


def _in_spans(spans: List[Tuple[int, int]], a: int) -> bool:
    i = bisect.bisect_right(spans, (a, float("inf"))) - 1
    return i >= 0 and spans[i][0] <= a < spans[i][1]


def _ends_function(word: Words, a: int, tail_jump: bool) -> bool:
    insn = decode(word(a), a)
    return (insn.name == "JR" and insn.rs == _RA) or (tail_jump and insn.name == "J")


def follows_return(word: Words, a: int, tail_jump: bool = False) -> bool:
    """True when the code before a (nop padding skipped) is a `jr ra` and its
    delay slot. An `addiu sp` that follows lui/mtc1 setup words is part of the
    function that starts at the lui, so it must not become an entry.
    tail_jump also accepts an unconditional `j`, which ends a function that
    tail-calls; it is only safe with independent evidence of an entry."""
    if decode(word(a - 4), a - 4).cat in (CAT_BRANCH, CAT_BRANCH_LIKELY, CAT_JUMP, CAT_JUMPR):
        return False  # a is the delay slot of that branch or jump
    b = a - 4
    for _ in range(_PAD_WORDS):
        if word(b) != 0:
            break
        b -= 4
    # b is the last non-nop word: the delay slot, or the jr itself when a nop
    # filled the delay slot.
    return _ends_function(word, b, tail_jump) or _ends_function(word, b - 4, tail_jump)


def prologue_seeds(word: Words, stretches: Iterable[Tuple[int, int]],
                   spans: List[Tuple[int, int]], known: Set[int]) -> List[int]:
    """Word-aligned `addiu sp, sp, -N` entries in JP-only code that lie outside
    every mapped function and follow a function return."""
    out = []
    for lo, hi in stretches:
        for a in range((lo + 3) & ~3, hi, 4):
            w = word(a)
            if (w >> 16) != 0x27BD or not w & 0x8000:
                continue
            if a in known or _in_spans(spans, a) or not follows_return(word, a):
                continue
            out.append(a)
    return out


def data_pointers(elf) -> Iterable[int]:
    """Every aligned word of the loaded non-code sections that hold data."""
    for sec in getattr(elf, "sections", ()):
        data = getattr(sec, "data", None)
        if (not sec.is_alloc or sec.is_exec or not data or sec.name in _SKIP_SECTIONS):
            continue
        n = len(data) // 4
        for v in struct.unpack("<%dI" % n, bytes(data[:n * 4])):
            if v & 3 == 0:
                yield v


def pointer_seeds(word: Words, values: Iterable[int], stretches: List[Tuple[int, int]],
                  spans: List[Tuple[int, int]], known: Set[int]) -> List[int]:
    """Data words that point into JP-only code, outside every mapped function and
    just after a function return. This finds leaf functions that the prologue
    rule cannot, e.g. virtual methods reached only through a table."""
    los = [lo for lo, _ in stretches]
    out = set()
    for v in values:
        i = bisect.bisect_right(los, v) - 1
        if v & 3 or i < 0 or v >= stretches[i][1] or v in known or v in out:
            continue
        if not _in_spans(spans, v) and follows_return(word, v, tail_jump=True):
            out.add(v)
    return sorted(out)


def _category(reason: str) -> str:
    if reason.startswith("uncertain"):
        return "uncertain"
    if reason.startswith("changed code in body-changed"):
        return "body-changed"
    return "other"


def handle(tr: Translator, src_dir: str) -> Dict[str, bytes]:
    raw = _read(src_dir)
    src = json.loads(raw)
    out: Dict[str, list] = {}
    known: Set[int] = {f.jp for f in tr.rmap.functions if f.jp is not None}
    per_list: Dict[str, int] = {}
    first_drop = len(tr.dropped)
    for key in LISTS:
        seen: Set[int] = set()
        for us in src.get(key, ()):
            where = "%s:0x%08X" % (key, us)
            jp = tr.addr(us)
            if jp is None:
                tr.drop(FILE, where, us)
                per_list[key] = per_list.get(key, 0) + 1
            elif jp in seen:
                tr.drop(FILE, where, us, "maps onto 0x%08X, already taken" % jp)
                per_list[key] = per_list.get(key, 0) + 1
            else:
                seen.add(jp)
                tr.kept(FILE)
        out[key] = sorted(seen)
        known |= seen
    for key, val in src.items():
        if key not in LISTS and key != REDERIVED:
            out[key] = val
            tr.copy(FILE)

    word = tr.jp_elf.word
    stretches, spans = jp_only_stretches(tr.rmap), merged_spans(tr)
    prologues = prologue_seeds(word, stretches, spans, known)
    pointers = pointer_seeds(word, data_pointers(tr.jp_elf), stretches, spans,
                             known | set(prologues))
    new = sorted(prologues + pointers)
    for a in new:
        how = "jp-only prologue" if a in prologues else "jp-only data pointer"
        tr.rederive(FILE, "%s:0x%08X" % (REDERIVED, a), None, a, how)
    out[REDERIVED] = new

    cats = {"uncertain": 0, "body-changed": 0, "other": 0}
    for e in tr.dropped[first_drop:]:
        cats[_category(e.text)] += 1
    print("ida_seeds: dropped %d (uncertain %d, body-changed %d, other %d), rederived %d"
          % (sum(cats.values()), cats["uncertain"], cats["body-changed"], cats["other"],
             len(new)))
    print("  rederived: prologue %d, data pointer %d" % (len(prologues), len(pointers)))
    print("  dropped by list: " + ", ".join("%s %d" % (k, per_list.get(k, 0)) for k in LISTS))
    return {FILE: dump_json(out, None, raw)}
