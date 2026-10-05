import bisect
import dataclasses
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

SCHEMA = 1

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Range:
    us_start: int
    us_end: int
    delta: int
    section: str
    source: str
    confidence: str


@dataclass
class Uncertain:
    us_start: int
    us_end: int
    section: str
    candidates: List[int]
    reason: str


@dataclass
class FuncMatch:
    us: int
    us_end: int
    name: str
    jp: Optional[int]
    jp_end: Optional[int]
    status: str
    method: str
    confidence: float
    similarity: float
    note: str = ""
    chunks: List[list] = field(default_factory=list)


def merge_ranges(ranges: List[Range]) -> List[Range]:
    """Sort; merge touching/overlapping rows with equal delta and section
    (keep the lower confidence, source of the first). Overlap with a different
    delta raises ValueError."""
    out: List[Range] = []
    for r in sorted((x for x in ranges if x.us_start < x.us_end),
                    key=lambda x: (x.us_start, x.us_end)):
        if out and r.us_start < out[-1].us_end:
            cur = out[-1]
            if r.delta != cur.delta or r.section != cur.section:
                raise ValueError(
                    "conflicting overlap: %08X-%08X delta %+#x %s vs %08X-%08X delta %+#x %s"
                    % (cur.us_start, cur.us_end, cur.delta, cur.section,
                       r.us_start, r.us_end, r.delta, r.section))
        if (out and r.us_start <= out[-1].us_end
                and r.delta == out[-1].delta and r.section == out[-1].section):
            cur = out[-1]
            cur.us_end = max(cur.us_end, r.us_end)
            if CONFIDENCE_ORDER[r.confidence] < CONFIDENCE_ORDER[cur.confidence]:
                cur.confidence = r.confidence
        else:
            out.append(dataclasses.replace(r))
    return out


class RegionMap:
    def __init__(self, us_info: dict, jp_info: dict, sections: list,
                 ranges: List[Range], uncertain: List[Uncertain],
                 functions: List[FuncMatch], stats: dict):
        self.us_info = us_info
        self.jp_info = jp_info
        self.sections = sections
        self.ranges = ranges
        # Sorted for bisect; validate() enforces that they are disjoint.
        self.uncertain = sorted(uncertain, key=lambda u: u.us_start)
        self.functions = functions
        self.stats = stats
        self._index()

    def _index(self) -> None:
        self._starts = [r.us_start for r in self.ranges]
        self._ustarts = [u.us_start for u in self.uncertain]
        # Every chunk (main one included) as (start, end, function), by start.
        rows: List[Tuple[int, int, FuncMatch]] = []
        for f in self.functions:
            rows.append((f.us, f.us_end, f))
            for c in f.chunks:
                if c[0] != f.us:
                    rows.append((c[0], c[1], f))
        rows.sort(key=lambda t: t[0])
        self._chunk_rows = rows
        self._chunk_starts = [t[0] for t in rows]

    def validate(self) -> None:
        # Pick up in-place edits to the lists before checking them.
        self.uncertain.sort(key=lambda u: u.us_start)
        self._index()
        prev_end = None
        for r in self.ranges:
            if r.us_start >= r.us_end:
                raise ValueError("empty range at %08X" % r.us_start)
            if prev_end is not None and r.us_start < prev_end:
                raise ValueError("ranges unsorted or overlapping at %08X" % r.us_start)
            prev_end = r.us_end
        for u in self.uncertain:
            if u.us_start >= u.us_end:
                raise ValueError("empty uncertain region at %08X" % u.us_start)
            # Ranges are sorted and disjoint, so only neighbours of u can overlap it.
            i = bisect.bisect_left(self._starts, u.us_end)
            if i > 0 and self.ranges[i - 1].us_end > u.us_start:
                raise ValueError("uncertain region %08X-%08X overlaps range %08X-%08X"
                                 % (u.us_start, u.us_end, self.ranges[i - 1].us_start,
                                    self.ranges[i - 1].us_end))
        for a, b in zip(self.uncertain, self.uncertain[1:]):
            if a.us_end > b.us_start:
                raise ValueError("uncertain regions %08X-%08X and %08X-%08X overlap"
                                 % (a.us_start, a.us_end, b.us_start, b.us_end))

    def range_for(self, addr: int) -> Optional[Range]:
        i = bisect.bisect_right(self._starts, addr) - 1
        if i >= 0 and addr < self.ranges[i].us_end:
            return self.ranges[i]
        return None

    def translate(self, addr: int) -> Optional[int]:
        r = self.range_for(addr)
        return None if r is None else addr + r.delta

    def uncertain_for(self, addr: int) -> Optional[Uncertain]:
        i = bisect.bisect_right(self._ustarts, addr) - 1
        if i >= 0 and addr < self.uncertain[i].us_end:
            return self.uncertain[i]
        return None

    def function_chunk_at(self, addr: int) -> Optional[Tuple[FuncMatch, int]]:
        """Function containing addr and the start of the containing chunk."""
        i = bisect.bisect_right(self._chunk_starts, addr) - 1
        if i >= 0:
            start, end, f = self._chunk_rows[i]
            if addr < end:
                return f, start
        return None

    def function_at(self, addr: int) -> Optional[FuncMatch]:
        hit = self.function_chunk_at(addr)
        return None if hit is None else hit[0]

    def section_name_at(self, addr: int) -> Optional[str]:
        for s in self.sections:
            if s["us_start"] <= addr < s["us_end"]:
                return s["name"]
        return None

    def to_json(self) -> dict:
        return {
            "schema": SCHEMA,
            "us": self.us_info,
            "jp": self.jp_info,
            "sections": self.sections,
            "ranges": [[r.us_start, r.us_end, r.delta, r.section, r.source, r.confidence]
                       for r in self.ranges],
            "uncertain": [dataclasses.asdict(u) for u in self.uncertain],
            "functions": [dataclasses.asdict(f) for f in self.functions],
            "stats": self.stats,
        }

    @classmethod
    def from_json(cls, d: dict) -> "RegionMap":
        if d.get("schema") != SCHEMA:
            raise ValueError("unsupported map schema: %r" % (d.get("schema"),))
        return cls(d["us"], d["jp"], d["sections"],
                   [Range(*row) for row in d["ranges"]],
                   [Uncertain(**u) for u in d["uncertain"]],
                   [FuncMatch(**f) for f in d["functions"]],
                   d["stats"])

    def save(self, path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as fp:
            json.dump(self.to_json(), fp, indent=1)

    @classmethod
    def load(cls, path: str) -> "RegionMap":
        with open(path) as fp:
            return cls.from_json(json.load(fp))
