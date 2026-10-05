import bisect
import os
import re
from typing import List, Tuple

RUN_RE = re.compile(r"^([0-9A-F]+)-([0-9A-F]+) delta=([+-]0x[0-9a-f]+) n=(\d+)$")

# A run this short is too weak to count as an expectation for its functions.
MIN_RUN = 3
# Code gaps up to this many bytes are alignment padding, not JP-only code.
JP_ONLY_MIN = 16
PREVIEW_BYTES = 32
# The report lists every row of these up to the cap, then a count of the rest.
LIST_CAP = 200


def load_heuristic(path: str) -> List[Tuple[int, int, int, int]]:
    """Parse the earlier delta listing into (lo, hi, delta, n) runs. Lines
    that are not runs (the trailing summary) are ignored."""
    runs = []
    with open(path) as fp:
        for line in fp:
            m = RUN_RE.match(line.strip())
            if m:
                runs.append((int(m.group(1), 16), int(m.group(2), 16),
                             int(m.group(3), 16), int(m.group(4))))
    return runs


def _delta_runs(functions) -> List[int]:
    """For each function, how many consecutive matched functions share its
    delta. A long run is strong support for the map's own answer."""
    n = len(functions)
    deltas = [None if f.jp is None else f.jp - f.us for f in functions]
    out = [0] * n
    i = 0
    while i < n:
        j = i + 1
        while j < n and deltas[j] == deltas[i]:
            j += 1
        for k in range(i, j):
            out[k] = (j - i) if deltas[i] is not None else 0
        i = j
    return out


def cross_check(runs, functions, min_n: int = MIN_RUN) -> dict:
    """Compare the map with the heuristic listing. Counts functions whose
    entry lies in a run with n >= min_n. `rows` holds every disagreement as a
    dict (our unmatched functions included)."""
    funcs = sorted(functions, key=lambda f: f.us)
    starts = [f.us for f in funcs]
    support = _delta_runs(funcs)
    agree = 0
    rows = []
    for lo, hi, delta, n in sorted(runs):
        if n < min_n:
            continue
        i = bisect.bisect_left(starts, lo)
        while i < len(funcs) and funcs[i].us <= hi:
            f = funcs[i]
            ours = None if f.jp is None else f.jp - f.us
            if ours == delta:
                agree += 1
            else:
                rows.append({"us": f.us, "name": f.name, "status": f.status,
                             "heuristic": delta, "ours": ours, "run": (lo, hi, n),
                             "our_run": support[i]})
            i += 1
    return {"agree": agree, "disagree": len(rows), "rows": rows}


def _fmt_delta(d) -> str:
    return "unmatched" if d is None else "%+#x" % d


def _ascii(raw: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in raw)


def _preview(label: str, elf, addr: int, size: int) -> str:
    raw = elf.read(addr, size)
    return "    %-9s %08X  %-*s  |%s|" % (label, addr, 3 * PREVIEW_BYTES - 1,
                                          " ".join("%02X" % b for b in raw), _ascii(raw))


def _capped(rows: list, fmt, lines: list) -> None:
    for r in rows[:LIST_CAP]:
        lines.append(fmt(r))
    if len(rows) > LIST_CAP:
        lines.append("  ... %d more" % (len(rows) - LIST_CAP))


def _heading(lines: list, title: str) -> None:
    if lines:
        lines.append("")
    lines.append("== %s ==" % title)


def _status_counts(rmap) -> dict:
    counts = {}
    for f in rmap.functions:
        counts[f.status] = counts.get(f.status, 0) + 1
    return counts


def _section_bytes(rmap, name: str) -> dict:
    out = {"high": 0, "medium": 0, "low": 0, "uncertain": 0}
    for r in rmap.ranges:
        if r.section == name:
            out[r.confidence] += r.us_end - r.us_start
    for u in rmap.uncertain:
        if u.section == name:
            out["uncertain"] += u.us_end - u.us_start
    return out


def _header(lines: list, rmap, extra: dict) -> None:
    us, jp = rmap.us_info, rmap.jp_info
    lines.append("Region map report")
    lines.append("us: %s sha256 %s" % (us["file"], us["sha256"][:16]))
    lines.append("jp: %s sha256 %s" % (jp["file"], jp["sha256"][:16]))
    if extra.get("elapsed") is not None:
        lines.append("elapsed: %.1f s" % extra["elapsed"])
    c = _status_counts(rmap)
    lines.append("functions: %d same, %d body-changed, %d unmatched"
                 % (c.get("same", 0), c.get("body-changed", 0), c.get("unmatched", 0)))
    st = rmap.stats
    data = st.get("data", {})
    # NOBITS ranges come from reference evidence and are always medium; only
    # the content walk's medium rows mark a dense in-place stretch.
    medium = sum(r.us_end - r.us_start for r in rmap.ranges
                 if r.confidence == "medium" and r.source == "content")
    inplace = sum(len(v.get("inplace_spans", [])) for v in data.values())
    lines.append("weak spots: %d content bytes at medium confidence, %d in-place spans, "
                 "%d uncertain regions (%d bytes), %d NOBITS boundaries"
                 % (medium, inplace, len(rmap.uncertain),
                    sum(u.us_end - u.us_start for u in rmap.uncertain),
                    len(st.get("nobits_boundaries", []))))
    if "evidence_agree" in st:
        lines.append("evidence: %d agree, %d disagree, %d unmapped; changed bodies "
                     "(not used): %d agree, %d disagree"
                     % (st["evidence_agree"], st["evidence_disagree"],
                        st.get("evidence_unmapped", 0), st.get("weak_agree", 0),
                        st.get("weak_disagree", 0)))
    h = st.get("heuristic")
    if h:
        lines.append("heuristic: %d agree, %d disagree (%.1f%%)"
                     % (h["agree"], h["disagree"],
                        100.0 * h["agree"] / max(h["agree"] + h["disagree"], 1)))


def _sections(lines: list, rmap) -> None:
    _heading(lines, "Sections")
    lines.append("%-18s %-17s %-17s %8s %8s %8s %8s %8s"
                 % ("section", "us", "jp", "delta", "high", "medium", "low", "uncert."))
    for s in rmap.sections:
        b = _section_bytes(rmap, s["name"])
        lines.append("%-18s %08X-%08X %08X-%08X %+#8x %8X %8X %8X %8X"
                     % (s["name"], s["us_start"], s["us_end"], s["jp_start"], s["jp_end"],
                        s["jp_start"] - s["us_start"], b["high"], b["medium"], b["low"],
                        b["uncertain"]))
    lines.append("(byte counts are US bytes by mapping confidence)")


def _code_segments(lines: list, rmap) -> None:
    segs = []            # [lo, hi, delta, medium bytes]
    for r in rmap.ranges:
        if r.section != ".text":
            continue
        med = r.us_end - r.us_start if r.confidence != "high" else 0
        if segs and segs[-1][1] == r.us_start and segs[-1][2] == r.delta:
            segs[-1][1] = r.us_end
            segs[-1][3] += med
        else:
            segs.append([r.us_start, r.us_end, r.delta, med])
    _heading(lines, "Code delta segments (.text, %d)" % len(segs))
    for lo, hi, d, med in segs:
        line = "%08X-%08X delta=%+#x size=%#x" % (lo, hi, d, hi - lo)
        if med:
            line += " medium=%#x" % med
        lines.append(line)


def _unmatched(lines: list, rmap) -> None:
    rows = [f for f in rmap.functions if f.status == "unmatched"]
    _heading(lines, "Unmatched functions (%d)" % len(rows))
    if not rows:
        lines.append("(none)")
    _capped(rows, lambda f: ("%08X %s size=%#x %s"
                             % (f.us, f.name, f.us_end - f.us, f.note)).rstrip(), lines)


def _body_changed(lines: list, rmap) -> None:
    rows = sorted((f for f in rmap.functions if f.status == "body-changed"),
                  key=lambda f: (f.similarity, f.us))
    _heading(lines, "Body-changed functions (%d)" % len(rows))
    lines.append("least similar first; sizes are bytes of the main chunk")
    _capped(rows, lambda f: "%08X -> %08X %s %#x %#x %.3f%s"
            % (f.us, f.jp, f.name, f.us_end - f.us, f.jp_end - f.jp, f.similarity,
               " (%s)" % f.note if f.note else ""), lines)


def _low_confidence(lines: list, rmap) -> None:
    summary = {}
    for f in rmap.functions:
        if f.status != "unmatched":
            key = (f.method, f.confidence if f.status == "same" else None)
            summary[key] = summary.get(key, 0) + 1
    rows = [f for f in rmap.functions
            if f.status == "same" and ((f.method == "exact" and f.confidence < 1.0)
                                       or f.confidence < 0.9)]
    _heading(lines, "Ambiguous and low-confidence matches (%d)" % len(rows))
    for (method, conf), n in sorted(summary.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        if conf is None:
            lines.append("method=%-5s body-changed       n=%d" % (method, n))
        else:
            lines.append("method=%-5s confidence=%.2f n=%d" % (method, conf, n))
    lines.append("listed: same functions matched ambiguously or below 0.90")
    _capped(rows, lambda f: "%08X -> %08X %s %s conf=%.2f%s"
            % (f.us, f.jp, f.name, f.method, f.confidence,
               " (%s)" % f.note if f.note else ""), lines)


def _call_conflicts(lines: list, cm) -> None:
    rows = list(getattr(cm, "conflicts", []) or [])
    _heading(lines, "Call conflicts (%d)" % len(rows))
    if cm is not None:
        cs = cm.stats
        lines.append("%d call pairs checked, %d conflicts"
                     % (cs.get("call_pairs_checked", 0), cs.get("call_conflicts", 0)))
    if not rows:
        lines.append("(none)")
    else:
        lines.append("site: US target -> JP of the matched target, JP target at the site")
    _capped(rows, lambda r: "%08X: %08X -> %08X, site calls %08X" % r, lines)


def _data_ranges(lines: list, rmap) -> None:
    data = rmap.stats.get("data", {})
    _heading(lines, "Data ranges")
    for s in rmap.sections:
        name = s["name"]
        if name == ".text":
            continue
        rows = [r for r in rmap.ranges if r.section == name]
        st = data.get(name, {})
        lines.append("")
        lines.append("%s %08X-%08X: %d ranges, deltas [%s]"
                     % (name, s["us_start"], s["us_end"], len(rows),
                        ", ".join("%+#x" % d for d in sorted({r.delta for r in rows}))))
        # Rows below high confidence are where the map is weaker; mark them.
        _capped(rows, lambda r: "  %08X-%08X delta=%+#x %s %s%s"
                % (r.us_start, r.us_end, r.delta, r.source, r.confidence,
                   "  <- weaker" if r.confidence != "high" else ""), lines)
        spans = st.get("inplace_spans", [])
        if spans:
            lines.append("  in-place spans (same length, content differs): %d spans, "
                         "%d changed words" % (len(spans), st.get("inplace_words", 0)))
            _capped(spans, lambda sp: "    %08X-%08X" % (sp[0], sp[1]), lines)


def _uncertain(lines: list, rmap, extra: dict) -> None:
    us_elf, jp_elf = extra.get("us_elf"), extra.get("jp_elf")
    _heading(lines, "Uncertain regions (%d)" % len(rmap.uncertain))
    if not rmap.uncertain:
        lines.append("(none)")
    for u in rmap.uncertain:
        lines.append("%08X-%08X %s size=%#x candidates=[%s] %s"
                     % (u.us_start, u.us_end, u.section, u.us_end - u.us_start,
                        ", ".join("%+#x" % c for c in u.candidates), u.reason))
        size = min(PREVIEW_BYTES, u.us_end - u.us_start)
        if us_elf is not None:
            lines.append(_preview("US", us_elf, u.us_start, size))
        if jp_elf is not None:
            for c in u.candidates:
                lines.append(_preview("JP%+#x" % c, jp_elf, u.us_start + c, size))


def _boundaries(lines: list, rmap) -> None:
    rows = rmap.stats.get("nobits_boundaries", [])
    _heading(lines, "Uncertain NOBITS boundaries (%d)" % len(rows))
    if not rows:
        lines.append("(none)")
    else:
        lines.append("the shift changes inside the region; no content locates it, "
                     "so the ranges keep the first shift")
    _capped(rows, lambda b: "%08X-%08X %s: %+#x -> %+#x" % (b[0], b[1], b[4], b[2], b[3]),
            lines)


def _evidence(lines: list, rmap) -> None:
    st = rmap.stats
    rows = st.get("evidence_disagreements", [])
    _heading(lines, "Evidence disagreements (%d)" % st.get("evidence_disagree", len(rows)))
    lines.append("code references from unchanged functions: %d agree, %d disagree, "
                 "%d unmapped"
                 % (st.get("evidence_agree", 0), st.get("evidence_disagree", 0),
                    st.get("evidence_unmapped", 0)))
    lines.append("code references from changed bodies (not used to build the map): "
                 "%d agree, %d disagree"
                 % (st.get("weak_agree", 0), st.get("weak_disagree", 0)))
    _capped(rows, lambda r: "  %08X: code says %08X, map says %08X (%d sites)" % tuple(r),
            lines)


def _heuristic(lines: list, result) -> None:
    _heading(lines, "Heuristic cross-check")
    if result is None:
        lines.append("(no heuristic file given)")
        return
    total = result["agree"] + result["disagree"]
    lines.append("functions inside runs with n >= %d: %d, agree %d, disagree %d (%.1f%%)"
                 % (MIN_RUN, total, result["agree"], result["disagree"],
                    100.0 * result["agree"] / max(total, 1)))
    if result["rows"]:
        lines.append("us name status heuristic ours (heuristic run; functions in our "
                     "run of equal delta)")
    for r in result["rows"]:
        lo, hi, n = r["run"]
        lines.append("%08X %s %s heuristic=%s ours=%s (run %08X-%08X n=%d; our run %d)"
                     % (r["us"], r["name"], r["status"], _fmt_delta(r["heuristic"]),
                        _fmt_delta(r["ours"]), lo, hi, n, r["our_run"]))


def _jp_only(lines: list, rmap) -> None:
    text = next((s for s in rmap.sections if s["name"] == ".text"), None)
    gaps = []
    if text is not None:
        cur = text["jp_start"]
        covered = sorted((r.us_start + r.delta, r.us_end + r.delta)
                         for r in rmap.ranges if r.section == ".text")
        for lo, hi in covered + [(text["jp_end"], text["jp_end"])]:
            if lo - cur > JP_ONLY_MIN:
                gaps.append((cur, lo))
            cur = max(cur, hi)
    _heading(lines, "JP-only code (%d)" % len(gaps))
    lines.append("JP .text stretches over %d bytes not covered by any range" % JP_ONLY_MIN)
    _capped(gaps, lambda g: "%08X-%08X size=%#x" % (g[0], g[1], g[1] - g[0]), lines)


def build_report(rmap, cm, extra: dict) -> str:
    extra = extra or {}
    lines: List[str] = []
    _header(lines, rmap, extra)
    _sections(lines, rmap)
    _code_segments(lines, rmap)
    _unmatched(lines, rmap)
    _body_changed(lines, rmap)
    _low_confidence(lines, rmap)
    _call_conflicts(lines, cm)
    _data_ranges(lines, rmap)
    _uncertain(lines, rmap, extra)
    _boundaries(lines, rmap)
    _evidence(lines, rmap)
    _heuristic(lines, extra.get("heuristic"))
    _jp_only(lines, rmap)
    return "\n".join(lines) + "\n"


def write_report(path: str, rmap, cm, extra: dict = None) -> None:
    """Write the text report. `extra` may carry elapsed seconds, the ELF
    files (for uncertain-region previews) and the cross_check result."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="\n") as fp:
        fp.write(build_report(rmap, cm, extra))
