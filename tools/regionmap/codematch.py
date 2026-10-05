import bisect
import json
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

from .mapfile import FuncMatch, Range, merge_ranges
from .normalize import Stream

MIN_EXACT_WORDS = 4      # shorter units are placed by references or gap diff only
SEARCH_WINDOW = 0x1000   # +- bytes around the expected JP address in the exact pass
AMBIGUOUS_MIN_WORDS = 8  # shorter units are too generic to pick among several hits
MIN_DIFF_BLOCK = 2       # equal blocks shorter than this are not worth a range


@dataclass
class Unit:
    us: int
    us_end: int
    func: int            # IDA function ea
    name: str
    main: bool
    jp: Optional[int] = None
    jp_end: Optional[int] = None
    status: str = "unmatched"   # internal extra state "located" = jp start known, end not yet
    method: str = "none"
    confidence: float = 0.0
    similarity: float = 0.0
    note: str = ""
    blocks: List[Tuple[int, int, int]] = field(default_factory=list)  # (us_word_off, jp_word_off, nwords) equal blocks


def load_units(ida_db_path: str, text_lo: int, text_hi: int) -> List[Unit]:
    """One unit per IDA chunk that lies inside [text_lo, text_hi), by US address."""
    with open(ida_db_path) as fp:
        funcs = json.load(fp)["functions"]
    units = []
    for f in funcs:
        for a, b in f["chunks"]:
            if b <= a or a < text_lo or b > text_hi:
                continue
            units.append(Unit(a, b, f["ea"], f["name"], a == f["ea"]))
    units.sort(key=lambda u: (u.us, u.us_end, u.func))
    return units


def _clear(u: Unit, note: str = "") -> None:
    u.jp = u.jp_end = None
    u.status, u.method = "unmatched", "none"
    u.confidence = u.similarity = 0.0
    u.note = note
    u.blocks = []


class CodeMatcher:
    def __init__(self, us: Stream, jp: Stream, units: List[Unit], log=print):
        self.us, self.jp, self.units, self.log = us, jp, units, log
        # Functions can share a tail chunk, so one address range may appear in
        # several units. Matching works on one representative per range and the
        # others copy its result (sync_duplicates).
        self.reps: List[Unit] = []
        self._dups: List[Tuple[Unit, Unit]] = []
        seen = {}
        for u in units:
            r = seen.setdefault((u.us, u.us_end), u)
            if r is u:
                self.reps.append(u)
            else:
                self._dups.append((r, u))
        self.stats = {"units": len(units), "exact": 0, "exact_ambiguous": 0,
                      "order_demoted": 0}
        self._ranges: Optional[List[Range]] = None
        self._range_starts: List[int] = []

    def sync_duplicates(self) -> None:
        for r, u in self._dups:
            u.jp, u.jp_end = r.jp, r.jp_end
            u.status, u.method = r.status, r.method
            u.confidence, u.similarity = r.confidence, r.similarity
            u.note, u.blocks = r.note, list(r.blocks)

    def _scan(self, body: List[int], center: int, lo_floor: int) -> List[int]:
        """JP word indexes within the search window where body matches."""
        jn = self.jp.norm
        n = len(body)
        win = SEARCH_WINDOW >> 2
        p = max(center - win, lo_floor, 0)
        stop = min(center + win, len(jn) - n)
        first, hits = body[0], []
        while p <= stop:
            try:
                p = jn.index(first, p, stop + 1)
            except ValueError:
                break
            if jn[p:p + n] == body:
                hits.append(p)
            p += 1
        return hits

    def match_exact(self) -> int:
        us, jp = self.us, self.jp
        un, jn = us.norm, jp.norm
        # Running shift in words between US and JP indexes. Both streams start
        # at their .text start, so the initial guess is the same index.
        delta = 0
        floor = 0                          # JP word index the next hit must not precede
        accepted: List[Tuple[Unit, int]] = []
        for u in self.reps:
            n = (u.us_end - u.us) >> 2
            if n < MIN_EXACT_WORDS:
                continue
            i = us.index(u.us)
            body = un[i:i + n]
            p0 = i + delta
            pos, conf, note, unique = None, 0.0, "", False
            if 0 <= p0 and p0 + n <= len(jn) and jn[p0:p0 + n] == body:
                pos, conf, unique = p0, 1.0, True
            else:
                hits = self._scan(body, p0, floor)
                if len(hits) == 1:
                    pos, conf, unique = hits[0], 0.95, True
                elif len(hits) > 1 and n >= AMBIGUOUS_MIN_WORDS:
                    pos = min(hits, key=lambda h: (abs(h - p0), h))
                    conf, note = 0.8, "ambiguous(%d)" % len(hits)
                    self.stats["exact_ambiguous"] += 1
            if pos is None:
                continue
            u.jp = jp.addr(pos)
            u.jp_end = u.jp + (u.us_end - u.us)
            u.status, u.method, u.confidence, u.similarity = "same", "exact", conf, 1.0
            u.note = note
            floor = pos + n
            if n >= AMBIGUOUS_MIN_WORDS or unique:
                delta = pos - i
            accepted.append((u, pos))

        # Link order: a unit placed before the end of the previous one is a
        # false hit (or the cause of one), so it is dropped.
        prev_end = None
        for u, pos in accepted:
            if prev_end is not None and pos < prev_end:
                _clear(u, "order")
                self.stats["order_demoted"] += 1
                continue
            prev_end = pos + ((u.us_end - u.us) >> 2)
        self.sync_duplicates()
        self._ranges = None
        self.stats["exact"] = sum(1 for u in self.reps if u.status == "same")
        return self.stats["exact"]

    def run(self) -> None:
        self.match_exact()
        for u in self.reps:
            if u.status == "located":
                _clear(u, "located but not resolved")
        self.sync_duplicates()
        self._ranges = None

    def aligned_pairs(self) -> Iterator[Tuple[int, int]]:
        """(US word index, JP word index) for words known to correspond."""
        for u in self.reps:
            if u.status == "same":
                i, j = self.us.index(u.us), self.jp.index(u.jp)
                for k in range((u.us_end - u.us) >> 2):
                    yield i + k, j + k
            elif u.status == "body-changed":
                i, j = self.us.index(u.us), self.jp.index(u.jp)
                for a, b, n in u.blocks:
                    for k in range(n):
                        yield i + a + k, j + b + k

    def code_ranges(self) -> List[Range]:
        if self._ranges is not None:
            return self._ranges
        out: List[Range] = []
        prev = None
        for u in self.reps:
            if u.status == "same":
                d = u.jp - u.us
                out.append(Range(u.us, u.us_end, d, ".text", "func", "high"))
                # Padding and code IDA did not type between two units with the
                # same shift is covered when its raw words agree.
                if prev is not None and prev.jp - prev.us == d and prev.us_end < u.us \
                        and self._raw_equal(prev.us_end, u.us, d):
                    out.append(Range(prev.us_end, u.us, d, ".text", "func", "high"))
                prev = u
            elif u.status == "body-changed":
                for a, b, n in u.blocks:
                    if n >= MIN_DIFF_BLOCK:
                        us0 = u.us + 4 * a
                        out.append(Range(us0, us0 + 4 * n, (u.jp + 4 * b) - us0,
                                         ".text", "diff", "medium"))
        self._ranges = merge_ranges(out)
        self._range_starts = [r.us_start for r in self._ranges]
        return self._ranges

    def _raw_equal(self, lo: int, hi: int, delta: int) -> bool:
        n = (hi - lo) >> 2
        i, j = self.us.index(lo), self.jp.index(lo + delta)
        if j < 0 or j + n > len(self.jp.raw):
            return False
        return self.us.raw[i:i + n] == self.jp.raw[j:j + n]

    def code_translate(self, addr: int) -> Optional[int]:
        rows = self.code_ranges()
        k = bisect.bisect_right(self._range_starts, addr) - 1
        if k >= 0 and addr < rows[k].us_end:
            return addr + rows[k].delta
        return None

    def functions(self) -> List[FuncMatch]:
        """One entry per IDA function, from its main chunk."""
        by_func = {}
        for u in self.units:
            by_func.setdefault(u.func, []).append(u)
        out = []
        for units in by_func.values():
            main = next((u for u in units if u.main), None)
            if main is None:
                continue
            status, note = main.status, main.note
            chunks = []
            if len(units) > 1:
                chunks = [[u.us, u.us_end, u.jp, u.status] for u in units]
                bad = [u for u in units if not u.main and u.status != "same"]
                if status == "same" and bad:
                    status = "body-changed"
                    note = "chunk %08X is %s" % (bad[0].us, bad[0].status)
            out.append(FuncMatch(main.us, main.us_end, main.name, main.jp, main.jp_end,
                                 status, main.method, main.confidence, main.similarity,
                                 note, chunks))
        out.sort(key=lambda f: f.us)
        return out
