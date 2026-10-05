"""Address translation helpers and bookkeeping shared by the config handlers."""
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from regionmap import RegionMap

# IDA auto names: sub_0028EAE8, loc_..., off_..., byte_...
_DUMMY = re.compile(r"^([A-Za-z]+_)([0-9A-Fa-f]{6,8})$")


@dataclass
class Event:
    file: str          # output file name
    where: str         # stable locator, e.g. "hooks:0x0031CF00"
    us: Optional[int]
    jp: Optional[int]
    text: str          # reason / method / note


def hexs(v: Optional[int]) -> Optional[str]:
    return None if v is None else "0x%08X" % v


def fmt_like(orig: str, value: int) -> str:
    """Format value as hex with the prefix, width and case of orig."""
    prefix = "0x" if orig[:2].lower() == "0x" else ""
    digits = orig[len(prefix):]
    width = len(digits)
    text = "%0*X" % (width, value)
    return prefix + (text.lower() if digits != digits.upper() else text)


class Translator:
    def __init__(self, rmap: RegionMap, us_elf, jp_elf):
        self.rmap = rmap
        self.us_elf = us_elf
        self.jp_elf = jp_elf
        self._main: Dict[int, object] = {}
        self._chunk: Dict[int, int] = {}
        for f in rmap.functions:
            self._main[f.us] = f
            for c in f.chunks:
                if c[0] != f.us and c[2] is not None:
                    self._chunk[c[0]] = c[2]
        self.counts: Dict[str, Dict[str, int]] = {}
        self.copied: Dict[str, int] = {}  # name/comment keys, a subset of kept
        self.dropped: List[Event] = []
        self.rederived: List[Event] = []
        self.notes: List[Event] = []
        self.failures: List[Event] = []

    # Lookups. None means unmapped; they never raise.

    def func_entry(self, us: int) -> Optional[int]:
        # A body-changed function's entry is placed by the function match, not
        # by the range table, so the two can disagree for its first word.
        fm = self._main.get(us)
        if fm is not None and fm.jp is not None:
            return fm.jp
        jp = self._chunk.get(us)
        if jp is not None:
            return jp
        return self.rmap.translate(us)

    def addr(self, us: int) -> Optional[int]:
        return self.func_entry(us)

    def end(self, us_end: int) -> Optional[int]:
        jp = self.rmap.translate(us_end - 1)
        return None if jp is None else jp + 1

    def why(self, us: int) -> str:
        u = self.rmap.uncertain_for(us)
        if u is not None:
            return "uncertain: %s" % u.reason
        fm = self.rmap.function_at(us)
        if fm is not None:
            if fm.status == "body-changed":
                return "changed code in body-changed %s" % fm.name
            if fm.jp is None:
                return "unmatched function %s" % fm.name
        return "outside every mapped range"

    def jp_spans(self, us_func: int) -> List[Tuple[int, int]]:
        fm = self._main.get(us_func)
        if fm is None:
            fm = self.rmap.function_at(us_func)
        if fm is None:
            return []
        spans: List[Tuple[int, int]] = []
        if fm.jp is not None and fm.jp_end is not None:
            spans.append((fm.jp, fm.jp_end))
        for c in fm.chunks:
            us_s, us_e, jp_s = c[0], c[1], c[2]
            if jp_s is None or (spans and us_s == fm.us):
                continue  # the main chunk is already listed from the match
            if len(c) > 3 and c[3] == "same":
                span = (jp_s, jp_s + us_e - us_s)
            else:
                jp_e = self.end(us_e)
                if jp_e is None:
                    continue
                span = (jp_s, jp_e)
            if span not in spans:
                spans.append(span)
        return spans

    def rename(self, name: str, us: int, jp: int) -> str:
        m = _DUMMY.match(name)
        if m is None or int(m.group(2), 16) != us:
            return name
        digits = m.group(2)
        text = "%0*X" % (len(digits), jp)
        if digits != digits.upper():
            text = text.lower()
        return m.group(1) + text

    # Bookkeeping, consumed by the manifest and the summary.

    def _count(self, file: str) -> Dict[str, int]:
        return self.counts.setdefault(file, {"kept": 0, "dropped": 0, "rederived": 0})

    def kept(self, file: str, n: int = 1) -> None:
        self._count(file)["kept"] += n

    def copy(self, file: str, n: int = 1) -> None:
        """Count n non-address entries that are copied unchanged."""
        self.kept(file, n)
        self.copied[file] = self.copied.get(file, 0) + n

    def drop(self, file: str, where: str, us: int, reason: Optional[str] = None) -> None:
        self._count(file)["dropped"] += 1
        self.dropped.append(Event(file, where, us, None,
                                  self.why(us) if reason is None else reason))

    def rederive(self, file: str, where: str, us: Optional[int], jp: int, how: str) -> None:
        self._count(file)["rederived"] += 1
        self.rederived.append(Event(file, where, us, jp, how))

    def note(self, file: str, where: str, us: int, jp: Optional[int], text: str) -> None:
        self.notes.append(Event(file, where, us, jp, text))

    def fail(self, file: str, where: str, us: Optional[int], reason: str) -> None:
        self.failures.append(Event(file, where, us, None, reason))


def dump_json(obj, indent: Optional[int], src: bytes) -> bytes:
    """Deterministic JSON; the trailing newline follows the US source file."""
    text = json.dumps(obj, indent=indent)
    if src.endswith(b"\n"):
        text += "\n"
    return text.encode("utf-8")


def _sort_key(e: Event):
    return (e.file, e.where, -1 if e.us is None else e.us)


def build_manifest(tr: Translator, outputs: Dict[str, bytes]) -> bytes:
    info = tr.rmap
    names = sorted(set(outputs) | set(tr.counts))
    manifest = {
        "generator": "tools/regionconfig",
        "us": {"file": info.us_info["file"], "sha256": info.us_info["sha256"]},
        "jp": {"file": info.jp_info["file"], "sha256": info.jp_info["sha256"]},
        "files": {n: dict(tr.counts.get(n, {"kept": 0, "dropped": 0, "rederived": 0}))
                  for n in names},
        "dropped": [{"file": e.file, "where": e.where, "us": hexs(e.us), "reason": e.text}
                    for e in sorted(tr.dropped, key=_sort_key)],
        "rederived": [{"file": e.file, "where": e.where, "us": hexs(e.us),
                       "jp": hexs(e.jp), "how": e.text}
                      for e in sorted(tr.rederived, key=_sort_key)],
        "notes": [{"file": e.file, "where": e.where, "us": hexs(e.us),
                   "jp": hexs(e.jp), "note": e.text}
                  for e in sorted(tr.notes, key=_sort_key)],
    }
    return (json.dumps(manifest, indent=1) + "\n").encode("utf-8")


def run_handlers(tr: Translator, handlers, src_dir: str) -> Dict[str, bytes]:
    """Run every handler and return all output files, manifest included.
    The caller must check tr.failures before writing anything."""
    outputs: Dict[str, bytes] = {}
    for h in handlers:
        for name, data in h(tr, src_dir).items():
            if name in outputs:
                raise ValueError("two handlers write %s" % name)
            outputs[name] = data
    outputs["manifest.json"] = build_manifest(tr, outputs)
    return outputs


def totals(tr: Translator, outputs: Dict[str, bytes]) -> Tuple[int, int, int, int]:
    names = [n for n in outputs if n != "manifest.json"]
    cs = [tr.counts.get(n, {"kept": 0, "dropped": 0, "rederived": 0}) for n in names]
    return (len(names), sum(c["kept"] for c in cs), sum(c["dropped"] for c in cs),
            sum(c["rederived"] for c in cs))


def display_path(path: str) -> str:
    """Path relative to the cwd when possible, with '/' separators."""
    try:
        rel = os.path.relpath(path)
    except ValueError:
        rel = path
    if rel.startswith(".."):
        rel = path
    return rel.replace("\\", "/")


def print_failures(tr: Translator, log=print) -> None:
    for e in sorted(tr.failures, key=_sort_key):
        log("FAIL %s %s us=%s: %s" % (e.file, e.where, hexs(e.us) or "none", e.text))
