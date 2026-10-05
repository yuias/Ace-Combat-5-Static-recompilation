"""Handler for ida_db.json: meta, segments, functions and names."""
import json
import os
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import regions
from ps2recomp.r5900 import decode

from .core import Translator, dump_json

FILE = "ida_db.json"
_LOAD = "LOAD"
_BOUND_WINDOW = 16  # instructions scanned before a jr for its range check
_RA = 31


def _hex(v: Optional[int]) -> str:
    return "nothing" if v is None else "0x%08X" % v


def _read(src_dir: str) -> bytes:
    path = os.path.join(src_dir, FILE)
    if not os.path.isfile(path):
        raise SystemExit("regionconfig: missing source file %s" % path.replace("\\", "/"))
    with open(path, "rb") as fp:
        return fp.read()


def _section(elf, ida_name: str):
    """ELF section for an IDA segment name (IDA upper-cases e.g. REGINFO)."""
    for cand in (ida_name, "." + ida_name.lower()):
        for s in elf.sections:
            if s.is_alloc and s.name == cand:
                return s
    return None


def _segments(tr: Translator, segs: List[dict]) -> List[dict]:
    """Named segments come from the JP section headers. A LOAD segment is the
    padding IDA creates between two sections, so it spans the JP gap."""
    out: List[Optional[dict]] = []
    for seg in segs:
        new = dict(seg)
        where = "segments[%s@0x%08X]" % (seg["name"], seg["start"])
        if seg["name"] == _LOAD:
            out.append(new)
            continue
        us_sec = _section(tr.us_elf, seg["name"])
        jp_sec = _section(tr.jp_elf, seg["name"])
        if (us_sec is None or us_sec.addr != seg["start"]
                or us_sec.addr + us_sec.size != seg["end"]):
            tr.fail(FILE, where, seg["start"], "segment does not match a US section header")
            out.append(None)
        elif jp_sec is None:
            tr.fail(FILE, where, seg["start"], "section missing in the JP executable")
            out.append(None)
        else:
            new["start"], new["end"] = jp_sec.addr, jp_sec.addr + jp_sec.size
            out.append(new)
    result: List[dict] = []
    for i, seg in enumerate(segs):
        new = out[i]
        if new is None:
            continue
        if seg["name"] == _LOAD:
            where = "segments[LOAD@0x%08X]" % seg["start"]
            if i == 0 or i + 1 == len(segs) or _LOAD in (segs[i - 1]["name"], segs[i + 1]["name"]):
                tr.fail(FILE, where, seg["start"], "LOAD gap is not between two sections")
                continue
            prev, nxt = out[i - 1], out[i + 1]
            if prev is None or nxt is None:
                continue  # a neighbour already failed
            new["start"], new["end"] = prev["end"], nxt["start"]
            if new["start"] > new["end"]:
                tr.fail(FILE, where, seg["start"], "JP sections overlap")
                continue
            if new["start"] == new["end"]:
                tr.drop(FILE, where, seg["start"], "empty gap between JP sections")
                continue
        result.append(new)
        tr.kept(FILE)
    return result


def _chunks(tr: Translator, fm, f: dict, jp_ea: int) -> List[list]:
    rows = {c[0]: c for c in fm.chunks}
    out = []
    for s, e in f["chunks"]:
        where = "functions[ea=0x%08X]:chunk 0x%08X" % (f["ea"], s)
        if s == f["ea"]:
            out.append([jp_ea, fm.jp_end])
            continue
        row = rows.get(s)
        jp = row[2] if row is not None else tr.rmap.translate(s)
        if jp is not None and row is not None and len(row) > 3 and row[3] == "same":
            jp_e: Optional[int] = jp + e - s
        else:
            jp_e = tr.end(e)
        if jp is None or jp_e is None:
            tr.drop(FILE, where, s)
        else:
            out.append([jp, jp_e])
    return out


def _functions(tr: Translator, funcs: List[dict]) -> List[dict]:
    by_us = {f.us: f for f in tr.rmap.functions}
    out = []
    owner: Dict[int, int] = {}
    for f in funcs:
        ea = f["ea"]
        where = "functions[ea=0x%08X]" % ea
        fm = by_us.get(ea)
        # fm.jp, not translate(): a body-changed function can have a new first word.
        if fm is None or fm.jp is None:
            tr.drop(FILE, where, ea, "function is not in the map" if fm is None else None)
            continue
        if fm.jp in owner:
            tr.fail(FILE, where, ea, "maps onto 0x%08X, already taken by 0x%08X"
                    % (fm.jp, owner[fm.jp]))
            continue
        owner[fm.jp] = ea
        new = dict(f)
        new["ea"] = fm.jp
        new["name"] = tr.rename(f["name"], ea, fm.jp)
        new["chunks"] = _chunks(tr, fm, f, fm.jp)
        out.append(new)
        tr.kept(FILE)
        if tr.rmap.translate(ea) != fm.jp:
            tr.note(FILE, where, ea, fm.jp, "entry placed by the function match; the "
                    "range table maps it to %s" % _hex(tr.rmap.translate(ea)))
    return out


def _names(tr: Translator, names: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, name in names.items():
        us = int(key)
        where = "names[%s]" % key
        jp = tr.addr(us)
        if jp is None:
            tr.drop(FILE, where, us)
        elif str(jp) in out:
            tr.drop(FILE, where, us, "maps onto 0x%08X, already taken" % jp)
        else:
            out[str(jp)] = tr.rename(name, us, jp)
            tr.kept(FILE)
    return out


class SwitchError(Exception):
    """A switch that cannot be translated safely; the text names the step."""


Words = Callable[[int], int]


def find_bound(word: Words, jr_ea: int) -> Optional[int]:
    """Immediate of the nearest SLTIU before a jr: the range check that
    guards the table."""
    for k in range(1, _BOUND_WINDOW + 1):
        a = jr_ea - 4 * k
        insn = decode(word(a), a)
        if insn.name == "SLTIU":
            return insn.imm
    return None


def read_table(word: Words, base: int, n: int, elbase: int) -> List[int]:
    return [(word(base + 4 * i) + elbase) & 0xFFFFFFFF for i in range(n)]


def self_check(us_word: Words, s: dict) -> Tuple[bool, bool]:
    """(table_ok, bound_ok): whether the recorded targets are exactly the US
    table, and whether the SLTIU bound before the jr equals ncases."""
    table = read_table(us_word, s["jumps"], s["ncases"], s["elbase"])
    return (set(table) == set(s["targets"]),
            find_bound(us_word, s["ea"]) == s["ncases"])


def _is_jump_reg(word: Words, ea: int) -> bool:
    insn = decode(word(ea), ea)
    return insn.name == "JR" and insn.rs != _RA


def _inside(addr: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(lo <= addr < hi for lo, hi in spans)


def jp_targets(mapped: List[Optional[int]], entries: List[int],
               in_text: Callable[[int], bool], spans: Sequence[Tuple[int, int]],
               check_spans: bool) -> Tuple[List[int], bool]:
    """Validate the JP table entries against the translated US targets and
    pick the target list. Returns (targets, rederived)."""
    for e in entries:
        if e & 3 or not in_text(e):
            raise SwitchError("step 3: JP table entry 0x%08X is not in .text" % e)
        if check_spans and not _inside(e, spans):
            raise SwitchError("step 3: JP table entry 0x%08X is outside the function" % e)
    have = set(entries)
    need = {m for m in mapped if m is not None}
    lost = sorted(need - have)
    if lost:
        raise SwitchError("step 3: translated US target 0x%08X is missing from the JP table"
                          % lost[0])
    if None not in mapped and have == need:
        return list(mapped), False  # type: ignore[arg-type]
    return list(dict.fromkeys(entries)), True


def translate_switch(tr: Translator, s: dict, us_word: Words, jp_word: Words,
                     in_text: Callable[[int], bool]) -> Tuple[dict, bool, List[str]]:
    """Translate one switch. Returns (switch, rederived, notes) or raises
    SwitchError. Only the word readers touch the binaries, so tests can use
    dict-backed fakes."""
    ea, translate = s["ea"], tr.rmap.translate
    table_ok, bound_ok = self_check(us_word, s)

    jp_ea = translate(ea)
    if jp_ea is None:
        raise SwitchError("step 2: ea is unmapped (%s); jr pairing is not implemented"
                          % tr.why(ea))
    if not _is_jump_reg(jp_word, jp_ea):
        raise SwitchError("step 2: 0x%08X is not a jr" % jp_ea)
    jp_func = tr.func_entry(s["func"])
    if jp_func is None:
        raise SwitchError("step 6: function 0x%08X is unmapped (%s)"
                          % (s["func"], tr.why(s["func"])))
    jp_jumps = translate(s["jumps"])
    if jp_jumps is None:
        raise SwitchError("step 3: table address is unmapped (%s); lui decode is not implemented"
                          % tr.why(s["jumps"]))
    elbase = s["elbase"]
    jp_elbase = elbase if elbase == 0 else translate(elbase)
    if jp_elbase is None:
        raise SwitchError("step 3: elbase is unmapped")

    mapped = [translate(t) for t in s["targets"]]
    rederived, n_jp = False, s["ncases"]
    if not table_ok:
        # The recorded targets are all we know, so every one must carry over.
        for t, m in zip(s["targets"], mapped):
            if m is None:
                raise SwitchError("step 4: target 0x%08X is unmapped (%s)" % (t, tr.why(t)))
        targets: List[int] = list(mapped)  # type: ignore[arg-type]
    else:
        if bound_ok:
            n_jp = find_bound(jp_word, jp_ea)
            if n_jp is None:
                raise SwitchError("step 3: no SLTIU bound before the JP jr")
        entries = read_table(jp_word, jp_jumps, n_jp, jp_elbase)
        fm = tr.rmap.function_at(s["func"])
        us_chunks = [] if fm is None else [(fm.us, fm.us_end)] + [(c[0], c[1]) for c in fm.chunks]
        # Targets outside the function (tail jumps) make the span test meaningless.
        check_spans = fm is not None and all(_inside(t, us_chunks) for t in s["targets"])
        targets, rederived = jp_targets(mapped, entries, in_text, tr.jp_spans(s["func"]),
                                        check_spans)

    notes: List[str] = []
    jp_start = translate(s["startea"])
    if jp_start is None:
        jp_start = jp_ea
        notes.append("startea 0x%08X is unmapped; using the jr" % s["startea"])
    new = dict(s)
    new.update(ea=jp_ea, func=jp_func, jumps=jp_jumps, elbase=jp_elbase, startea=jp_start,
               ncases=n_jp, targets=targets)
    return new, rederived, notes


def _switches(tr: Translator, switches: List[dict]) -> List[dict]:
    us_word, jp_word = tr.us_elf.word, tr.jp_elf.word
    ranges = tr.jp_elf.text_ranges()
    in_text = lambda a: _inside(a, ranges)
    checks = [self_check(us_word, s) for s in switches]
    n_kept = n_re = n_fail = 0
    out: List[dict] = []
    owner: Dict[int, int] = {}
    for s in switches:
        where = "switches[ea=0x%08X]" % s["ea"]
        try:
            new, rederived, notes = translate_switch(tr, s, us_word, jp_word, in_text)
            if new["ea"] in owner:
                raise SwitchError("step 2: maps onto 0x%08X, already taken by 0x%08X"
                                  % (new["ea"], owner[new["ea"]]))
        except SwitchError as e:
            tr.fail(FILE, where, s["ea"], str(e))
            n_fail += 1
            continue
        owner[new["ea"]] = s["ea"]
        for text in notes:
            tr.note(FILE, where, s["ea"], new["ea"], text)
        if rederived:
            tr.rederive(FILE, where, s["ea"], new["ea"], "jump table")
            n_re += 1
        else:
            tr.kept(FILE)
            n_kept += 1
        out.append(new)
    print("switches: %d total, table_ok %d, table_bad %d, bound_ok %d, bound_bad %d"
          % (len(switches), sum(t for t, _ in checks), sum(not t for t, _ in checks),
             sum(b for _, b in checks), sum(not b for _, b in checks)))
    print("switches: kept %d, rederived %d, failed %d" % (n_kept, n_re, n_fail))
    return out


def handle(tr: Translator, src_dir: str, switches: bool = True) -> Dict[str, bytes]:
    raw = _read(src_dir)
    db = json.loads(raw)
    segments = _segments(tr, db["segments"])
    meta = dict(db["meta"])
    meta["entry"] = tr.jp_elf.entry
    if segments:
        meta["min_ea"] = min(s["start"] for s in segments)
        meta["max_ea"] = max(s["end"] for s in segments)
    meta["input"] = regions.JP.exe_name
    parts = {
        "meta": meta,
        "segments": segments,
        "functions": _functions(tr, db["functions"]),
        "switches": _switches(tr, db["switches"]) if switches else [],
        "names": _names(tr, db["names"]),
    }
    out = {k: parts[k] for k in db}  # keep the US key order
    return {FILE: dump_json(out, None, raw)}
