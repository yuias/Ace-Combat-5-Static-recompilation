import bisect
import difflib
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
MAX_GAP_WORDS = 8192     # per side; larger gaps are not diffed (time budget knob)
BODY_CHANGED_MIN = 0.5   # similarity ratio below this is a mismatch, not a changed body


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
                      "located": 0, "placed_by_call": 0, "gap_filled": 0,
                      "diff_same": 0, "diff_changed": 0, "gaps_too_large": 0}
        self._gaps_done = set()
        self._rejected = set()
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
        for i, j in self.aligned_pairs():
            ref = self._code_ref(i, j)
            if ref is not None:
                yield (i,) + ref

    def _code_ref(self, i: int, j: int) -> Optional[Tuple[int, int, int]]:
        """(kind, US target, JP target) when US word i and JP word j carry the
        same kind of reference into code."""
        us, jp = self.us, self.jp
        a = us.refs.get(i)
        if a is None:
            return None
        b = jp.refs.get(j)
        if b is None or b[0] != a[0] or a[0] not in (REF_CALL, REF_ABS):
            return None
        # Absolute references only count when both targets are code; this
        # also drops constants outside the image that merely look like addresses.
        if not (us.base <= a[1] < us.addr(len(us.raw))
                and jp.base <= b[1] < jp.addr(len(jp.raw))):
            return None
        # A misaligned target is a data constant, not a code address.
        if (a[1] | b[1]) & 3:
            return None
        return a[0], a[1], b[1]

    def _unit_starts(self) -> dict:
        """US address -> the unit starting there (the main chunk if several)."""
        starts = {}
        for u in self.reps:
            if u.us not in starts or u.main:
                starts[u.us] = u
        return starts

    def _trim_blocks(self) -> None:
        """Equal blocks pair calls whose targets are masked, so a block can pair
        two calls to different functions. Once the targets are placed, split the
        blocks of body-changed units at every call that disagrees with them."""
        starts = self._unit_starts()
        for u in self.reps:
            if u.status != "body-changed":
                continue
            i, j = self.us.index(u.us), self.jp.index(u.jp)
            out = []
            for a, b, n in u.blocks:
                lo = None                     # start of the run being kept
                for k in range(n + 1):
                    bad = False
                    if k < n:
                        ref = self._code_ref(i + a + k, j + b + k)
                        v = starts.get(ref[1]) if ref else None
                        bad = v is not None and v.jp is not None and v.jp != ref[2]
                    if k < n and not bad:
                        if lo is None:
                            lo = k
                    elif lo is not None:
                        out.append((a + lo, b + lo, k - lo))
                        lo = None
            u.blocks = out

    def _place(self, u: Unit, target: int, limit: int) -> None:
        """Put a unit at a JP address learned from a reference. It is final only
        when its whole body fits below limit; otherwise the gap diff decides
        where it ends."""
        n = (u.us_end - u.us) >> 2
        i, j = self.us.index(u.us), self.jp.index(target)
        u.jp, u.method, u.note = target, "call", ""
        if target + 4 * n <= limit and j + n <= len(self.jp.norm) \
                and self.jp.norm[j:j + n] == self.us.norm[i:i + n]:
            u.jp_end = target + (u.us_end - u.us)
            u.status, u.confidence, u.similarity = "same", 0.9, 1.0
            self.stats["placed_by_call"] += 1
        else:
            u.jp_end, u.status = None, "located"
            self.stats["located"] += 1

    def _propagate_once(self) -> int:
        reps = self.reps
        starts = self._unit_starts()
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
                lo = u.jp_end if u.jp_end is not None else u.jp + (u.us_end - u.us)
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
            if (u.us, t) in self._rejected:
                continue
            if not (max(lows[k], last_end) <= t < highs[k]):
                u.note = "call target out of order"
                continue
            self._place(u, t, highs[k])
            last_end = u.jp_end if u.jp_end is not None else u.jp + (u.us_end - u.us)
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
                        p.status, p.method, p.note = "same", "diff", "inside equal-shift gap"
                        p.confidence, p.similarity = 0.9, 1.0
                        filled += 1
            prev, pending = u, []
        self.stats["gap_filled"] += filled
        return filled

    def propagate_refs(self) -> int:
        """One pass through the references of already-aligned code, then one
        through equal-shift gaps. Returns the number of units placed; run()
        repeats it."""
        n = self._propagate_once()
        n += self._fill_equal_gaps()
        self.sync_duplicates()
        self._ranges = None
        return n

    def _reject(self, u: Unit, note: str, remember: bool = True) -> None:
        # A call-derived start whose body the diff refused is remembered so that
        # the next round does not place the unit there again. Refusals for lack
        # of space say nothing about the body, so they are not remembered.
        if remember and u.jp is not None:
            self._rejected.add((u.us, u.jp))
        _clear(u, note)

    def _diff_segment(self, units: List[Unit], s_us: int, s_jp: int,
                      e_us: int, e_jp: int) -> int:
        """Align the US words [s_us, e_us) with the JP words [s_jp, e_jp) and
        resolve the units inside. A located first unit starts at its own JP
        address, which is also s_jp. Returns the number of units newly matched."""
        key = (s_us, s_jp, e_us, e_jp, tuple((u.us, u.us_end, u.jp) for u in units))
        if key in self._gaps_done:
            return 0
        self._gaps_done.add(key)
        nu, nj = (e_us - s_us) >> 2, (e_jp - s_jp) >> 2
        if nj <= 0:
            for u in units:
                # Keep the more specific reason from an earlier, wider diff.
                self._reject(u, u.note if u.note == "deleted" else "no JP space", False)
            return 0
        if nu > MAX_GAP_WORDS or nj > MAX_GAP_WORDS:
            self.log("gap too large: us %08X-%08X (%d words), jp %08X-%08X (%d words), "
                     "%d units" % (s_us, e_us, nu, s_jp, e_jp, nj, len(units)))
            self.stats["gaps_too_large"] += 1
            for u in units:
                self._reject(u, "gap too large", False)
            return 0

        un, jn = self.us.norm, self.jp.norm
        si, sj = self.us.index(s_us), self.jp.index(s_jp)
        ops = difflib.SequenceMatcher(None, un[si:si + nu], jn[sj:sj + nj],
                                      autojunk=False).get_opcodes()
        i1s = [op[1] for op in ops]

        def locate(off):
            """(JP word offset, op tag) for a US word offset; offset None when deleted."""
            tag, i1, _, j1, j2 = ops[bisect.bisect_right(i1s, off) - 1]
            if tag == "equal":
                return j1 + off - i1, tag
            if tag == "replace":
                return j1 + min(off - i1, j2 - j1 - 1), tag
            return None, tag

        starts = []
        for u in units:
            a = (u.us - s_us) >> 2
            if not 0 <= a < nu:             # overlaps an anchor
                starts.append(None)
            else:
                starts.append((u.jp - s_jp) >> 2 if u.jp is not None else locate(a)[0])
        newly = 0
        for k, u in enumerate(units):
            was_final = u.jp_end is not None
            a, b = (u.us - s_us) >> 2, (u.us_end - s_us) >> 2
            start = starts[k]
            # Next unit that has a JP start bounds this one from above.
            nxt = next((s for s in starts[k + 1:] if s is not None), nj)
            if start is None or b > nu:
                self._reject(u, "deleted")
                continue
            end, tag = locate(b - 1)
            end = end + 1 if tag == "equal" else nxt
            end = min(end, nxt)
            if end <= start:
                self._reject(u, "deleted")
                continue
            ub, jb = un[si + a:si + b], jn[sj + start:sj + end]
            located = u.jp is not None
            if ub == jb:
                u.status, u.method, u.confidence, u.similarity = "same", "diff", 0.9, 1.0
                u.note, u.blocks = "", []
                self.stats["diff_same"] += 1
            else:
                m = difflib.SequenceMatcher(None, ub, jb, autojunk=False)
                ratio = m.ratio()
                if ratio < BODY_CHANGED_MIN:
                    self._reject(u, "best guess jp=%08X ratio=%.2f" % (s_jp + 4 * start, ratio))
                    continue
                u.status, u.method = "body-changed", "call" if located else "diff"
                u.confidence = u.similarity = ratio
                u.note = ""
                u.blocks = [(x, y, n) for x, y, n in m.get_matching_blocks() if n]
                self.stats["diff_changed"] += 1
            u.jp = s_jp + 4 * start
            u.jp_end = s_jp + 4 * end
            if not was_final:
                newly += 1
        return newly

    def match_gaps(self) -> int:
        """Diff the code between matched units and resolve the units in it.
        Matched units are fixed anchors; a located unit splits a gap at its
        start. Returns the number of units newly matched."""
        us_end = self.us.addr(len(self.us.raw))
        jp_end = self.jp.addr(len(self.jp.raw))
        segs = []
        s_us, s_jp, pend = self.us.base, self.jp.base, []
        for u in self.reps:
            if u.jp_end is not None:
                segs.append((s_us, s_jp, u.us, u.jp, pend))
                s_us, s_jp, pend = u.us_end, u.jp_end, []
            elif u.jp is not None:
                segs.append((s_us, s_jp, u.us, u.jp, pend))
                s_us, s_jp, pend = u.us, u.jp, [u]
            else:
                pend.append(u)
        segs.append((s_us, s_jp, us_end, jp_end, pend))
        total = 0
        for seg in segs:
            if seg[4]:
                total += self._diff_segment(seg[4], *seg[:4])
        self._trim_blocks()
        self.sync_duplicates()
        self._ranges = None
        return total

    def run(self) -> None:
        self.match_exact()
        # Call and gap passes feed each other, so they repeat until stable.
        for _ in range(MAX_ROUNDS):
            n = self.propagate_refs()
            n += self.match_gaps()
            if not n:
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
