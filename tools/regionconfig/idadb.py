"""Handler for ida_db.json: meta, segments, functions and names."""
import json
import os
from typing import Dict, List, Optional

import regions

from .core import Translator, dump_json

FILE = "ida_db.json"
_LOAD = "LOAD"


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
        "switches": [],
        "names": _names(tr, db["names"]),
    }
    out = {k: parts[k] for k in db}  # keep the US key order
    if switches:
        tr.fail(FILE, "switches", None, "switch translation not implemented")
    return {FILE: dump_json(out, None, raw)}
