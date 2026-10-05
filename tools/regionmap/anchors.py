import json
import os
import re
from dataclasses import dataclass
from typing import List

from .common import words

# Guest addresses in the runtime sources are written as 0x00xxxxxx constants;
# the leading 0x00[1-4] already excludes most instruction words and values.
ADDR_RE = re.compile(r"\b0x00[1-4][0-9A-Fa-f]{5}u?\b")


@dataclass
class Anchor:
    addr: int
    origins: List[str]
    kind: str                       # "code" (.text) or "data"


def _section_at(us_sections, addr: int):
    for s in us_sections:
        if s["us_start"] <= addr < s["us_end"]:
            return s["name"]
    return None


def _config_keys(path: str, origin_name: str, tables_of, out: list) -> None:
    """Add the address keys of the {address: handler} tables found in a config
    file; the handler name makes the origin."""
    if not os.path.isfile(path):
        return
    with open(path) as fp:
        data = json.load(fp)
    for handlers in tables_of(data):
        for key, handler in handlers.items():
            out.append((int(key, 16), "%s:%s" % (origin_name, handler)))


def _runtime_files(runtime_dir: str):
    for sub, ext in (("src", ".c"), ("include", ".h")):
        for root, dirs, files in os.walk(os.path.join(runtime_dir, sub)):
            dirs.sort()
            for name in sorted(files):
                if name.endswith(ext):
                    yield os.path.join(root, name)


def collect(config_dir: str, runtime_dir: str, us_sections) -> List[Anchor]:
    """Gather every US address that hooks, overrides and runtime constants
    depend on, deduplicated by address with all origins kept. Constants are
    kept only when they fall inside a US allocated section."""
    found = []      # (addr, origin)
    _config_keys(os.path.join(config_dir, "hooks.json"), "config/hooks.json",
                 lambda d: [d], found)
    _config_keys(os.path.join(config_dir, "report.json"), "config/report.json",
                 lambda d: [d.get(k, {}) for k in ("applied_overrides", "applied_hooks")],
                 found)

    prefix = os.path.basename(os.path.normpath(runtime_dir))
    for path in _runtime_files(runtime_dir):
        rel = "%s/%s" % (prefix, os.path.relpath(path, runtime_dir).replace(os.sep, "/"))
        with open(path, encoding="utf-8", errors="replace") as fp:
            for n, line in enumerate(fp, 1):
                for m in ADDR_RE.finditer(line):
                    addr = int(m.group(0).rstrip("uU"), 16)
                    if _section_at(us_sections, addr) is not None:
                        found.append((addr, "%s:%d" % (rel, n)))

    by_addr = {}
    for addr, origin in found:
        a = by_addr.get(addr)
        if a is None:
            sec = _section_at(us_sections, addr)
            a = by_addr[addr] = Anchor(addr, [], "code" if sec == ".text" else "data")
        if origin not in a.origins:
            a.origins.append(origin)
    return [by_addr[k] for k in sorted(by_addr)]


def _prologue(us_elf, jp_elf, us_addr: int, jp_addr: int):
    try:
        return words(us_elf, us_addr, us_addr + 8), words(jp_elf, jp_addr, jp_addr + 8)
    except ValueError:
        return None


def _reason(rmap, addr: int, section, hit) -> str:
    u = rmap.uncertain_for(addr)
    if u is not None:
        return "uncertain region (%s)" % u.reason
    if hit is not None:
        f = hit[0]
        if f.status == "unmatched":
            return "function unmatched%s" % (": " + f.note if f.note else "")
        if f.status == "body-changed":
            return "no range inside the changed body%s" % (": " + f.note if f.note else "")
        return "no range covers the address"
    return "no range covers the address (%s)" % (section or "outside sections")


def check(anchors, rmap, us_elf, jp_elf) -> List[dict]:
    """Resolve each anchor through the map. A function entry also gets its
    first two raw words compared between the executables, which verifies the
    prologue tables in the runtime without parsing the C."""
    rows = []
    for a in anchors:
        jp = rmap.translate(a.addr)
        rng = rmap.range_for(a.addr)
        hit = rmap.function_chunk_at(a.addr)
        f = hit[0] if hit else None
        section = rmap.section_name_at(a.addr)
        row = {"addr": a.addr, "kind": a.kind, "origins": list(a.origins),
               "section": section, "jp": jp, "mapped": jp is not None,
               "function": f.name if f else None, "status": f.status if f else None,
               "offset": a.addr - hit[1] if hit else None,
               "confidence": rng.confidence if rng else None,
               "reason": "" if jp is not None else _reason(rmap, a.addr, section, hit),
               "prologue": None}
        if jp is not None and f is not None and f.us == a.addr and a.kind == "code":
            pro = _prologue(us_elf, jp_elf, a.addr, jp)
            if pro is not None:
                row["prologue"] = {"us": pro[0], "jp": pro[1], "same": pro[0] == pro[1]}
        rows.append(row)
    return rows


def summarize(rows) -> dict:
    out = {"code_total": 0, "code_mapped": 0, "data_total": 0, "data_mapped": 0,
           "prologue_checked": 0, "prologue_mismatches": 0}
    for r in rows:
        out[r["kind"] + "_total"] += 1
        out[r["kind"] + "_mapped"] += r["mapped"]
        if r["prologue"] is not None:
            out["prologue_checked"] += 1
            out["prologue_mismatches"] += not r["prologue"]["same"]
    return out
