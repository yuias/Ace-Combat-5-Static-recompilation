import bisect
import json
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

from .mapfile import FuncMatch, Range, merge_ranges
from .normalize import REF_ABS, REF_CALL, Stream

MIN_EXACT_WORDS = 4      # shorter units are placed by references or gap diff only
SEARCH_WINDOW = 0x1000   # +- bytes around the expected JP address in the exact pass
AMBIGUOUS_MIN_WORDS = 8  # shorter units are too generic to pick among several hits
MIN_DIFF_BLOCK = 2       # equal blocks shorter than this are not worth a range
MAX_ROUNDS = 4           # passes that feed each other stop here if still changing


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
                      "order_demoted": 0, "call_pairs_checked": 0, "call_conflicts": 0,
                      "located": 0, "placed_by_call": 0, "gap_filled": 0}
        # (site, US target, JP start of the matched target, JP target at the site)
        self.conflicts: List[Tuple[int, int, int, int]] = []
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

    def _ref_pairs(self) -> Iterator[Tuple[int, int, int, int]]:
        """(site US word index, kind, US target, JP target) where an aligned US
        word and its JP counterpart carry the same kind of code reference."""
        us, jp = self.us, self.jp
        ulo, uhi = us.base, us.addr(len(us.raw))
        jlo, jhi = jp.base, jp.addr(len(jp.raw))
        for i, j in self.aligned_pairs():
            a = us.refs.get(i)
            if a is None:
                continue
            b = jp.refs.get(j)
            if b is None or b[0] != a[0] or a[0] not in (REF_CALL, REF_ABS):
                continue
            # Absolute references only count when both targets are code; this
            # also drops constants outside the image that merely look like addresses.
            if not (ulo <= a[1] < uhi and jlo <= b[1] < jhi):
                continue
            yield i, a[0], a[1], b[1]

    def _place(self, u: Unit, target: int) -> None:
        """Put a unit at a JP address learned from a reference."""
        n = (u.us_end - u.us) >> 2
        i, j = self.us.index(u.us), self.jp.index(target)
        u.jp, u.method, u.note = target, "call", ""
        if j + n <= len(self.jp.norm) and self.jp.norm[j:j + n] == self.us.norm[i:i + n]:
            u.jp_end = target + (u.us_end - u.us)
            u.status, u.confidence, u.similarity = "same", 0.9, 1.0
            self.stats["placed_by_call"] += 1
        else:
            u.jp_end, u.status = None, "located"
            self.stats["located"] += 1

    def _propagate_once(self) -> int:
        reps = self.reps
        starts = {}
        for u in reps:
            if u.us not in starts or u.main:
                starts[u.us] = u
        cands = {}
        pairs = conflicts = 0
        self.conflicts = []
        for i, _, ut, jt in self._ref_pairs():
            v = starts.get(ut)
            if v is None:
                continue
            pairs += 1
            if v.jp is not None:
                if v.jp != jt:
                    conflicts += 1
                    self.conflicts.append((self.us.addr(i), ut, v.jp, jt))
            else:
                cands.setdefault(v.us, set()).add(jt)
        self.stats["call_pairs_checked"], self.stats["call_conflicts"] = pairs, conflicts
        if not cands:
            return 0

        # Link order: a unit lies after the end of the nearest matched unit
        # before it and before the start of the nearest one after it.
        lows, highs = [], [0] * len(reps)
        lo = self.jp.base
        for u in reps:
            lows.append(lo)
            if u.jp is not None:
                lo = u.jp_end if u.jp_end is not None else u.jp + 4
        hi = self.jp.addr(len(self.jp.raw))
        for k in range(len(reps) - 1, -1, -1):
            highs[k] = hi
            if reps[k].jp is not None:
                hi = reps[k].jp

        placed = 0
        last_end = 0                    # end of the unit placed last in this round
        for k, u in enumerate(reps):
            c = cands.get(u.us)
            if c is None or u.jp is not None or starts[u.us] is not u:
                continue
            if len(c) > 1:
                u.note = "call targets disagree"
                continue
            t = next(iter(c))
            if not (max(lows[k], last_end) <= t < highs[k]):
                u.note = "call target out of order"
                continue
            self._place(u, t)
            last_end = u.jp_end if u.jp_end is not None else u.jp + 4
            placed += 1
        return placed

    def _fill_equal_gaps(self) -> int:
        """Units between two matched units that keep the same shift, where the
        raw words of the whole gap agree, are present at that shift too. This
        places short stubs that no reference reaches."""
        filled = 0
        prev, pending = None, []
        for u in self.reps:
            if u.jp_end is None:
                if u.jp is None:
                    pending.append(u)
                else:                       # located: its span is not known yet
                    prev, pending = None, []
                continue
            d = u.jp - u.us
            if prev is not None and pending and prev.us_end <= u.us \
                    and prev.jp_end - prev.us_end == d \
                    and self._raw_equal(prev.us_end, u.us, d):
                for p in pending:
                    if p.us >= prev.us_end and p.us_end <= u.us:
                        p.jp, p.jp_end = p.us + d, p.us_end + d
                        p.status, p.method, p.note = "same", "exact", "inside equal-shift gap"
                        p.confidence, p.similarity = 0.9, 1.0
                        filled += 1
            prev, pending = u, []
        self.stats["gap_filled"] += filled
        return filled

    def propagate_refs(self) -> int:
        """Place units through the references of already-aligned code, then
        through equal-shift gaps, until nothing changes. Returns the number of
        units placed."""
        total = 0
        while True:
            n = self._propagate_once()
            n += self._fill_equal_gaps()
            if not n:
                break
            total += n
        self.sync_duplicates()
        self._ranges = None
        return total

    def run(self) -> None:
        self.match_exact()
        # Call and gap passes feed each other, so they repeat until stable.
        for _ in range(MAX_ROUNDS):
            if not self.propagate_refs():
                break
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
        prev = None                       # (us_end, jp_end) of the last placed unit
        for u in self.reps:
            if u.jp_end is None:
                continue
            d = u.jp - u.us
            # Padding and code IDA did not type between two placed units that
            # meet at the same shift is covered when its raw words agree.
            if prev is not None and prev[0] < u.us and prev[1] - prev[0] == d \
                    and self._raw_equal(prev[0], u.us, d):
                out.append(Range(prev[0], u.us, d, ".text", "func", "high"))
            prev = (u.us_end, u.jp_end)
            if u.status == "same":
                out.append(Range(u.us, u.us_end, d, ".text", "func", "high"))
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
            status, note, conf = main.status, main.note, main.confidence
            chunks = []
            if len(units) > 1:
                chunks = [[u.us, u.us_end, u.jp, u.status] for u in units]
                bad = [u for u in units if not u.main and u.status != "same"]
                if status == "same" and bad:
                    status = "body-changed"
                    note = "chunk %08X is %s" % (bad[0].us, bad[0].status)
                    conf = min(u.confidence for u in units)
            out.append(FuncMatch(main.us, main.us_end, main.name, main.jp, main.jp_end,
                                 status, main.method, conf, main.similarity,
                                 note, chunks))
        out.sort(key=lambda f: f.us)
        return out
