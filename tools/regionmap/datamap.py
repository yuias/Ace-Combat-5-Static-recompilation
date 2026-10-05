import bisect
from collections import Counter
from dataclasses import replace
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from .common import SecPair, words
from .mapfile import Range, Uncertain, merge_ranges
from .normalize import REF_ABS, REF_GP

RESYNC_WORDS = 8        # consecutive matching words required to resync
RESYNC_SCAN = 0x2000    # bytes scanned forward for a resync point
RESYNC_WINDOW = 0x1000  # +- bytes around pos+d searched in JP
MAX_CANDIDATES = 16     # JP positions tried per scanned word (common words repeat a lot)
POINTER_SLACK = 0x1000  # tolerated difference for pointers the code map cannot translate
DENSE_WORDS = 32        # lookahead for the dense in-place check
DENSE_MIN_NONZERO = 6   # non-zero words needed in that window to trust it
DENSE_NUM, DENSE_DEN = 3, 5  # required share of agreeing non-zero words


def _inside(spans: List[Tuple[int, int]], addr: int) -> bool:
    for lo, hi in spans:
        if lo <= addr < hi:
            return True
    return False


def evidence_pairs(cm, sections: List[SecPair]) -> Iterator[Tuple[int, int]]:
    """(US target, JP target) of data references seen at aligned code words.
    Only REF_ABS/REF_GP pairs of the same kind count; the US target must lie
    in a non-.text section and the JP target in some JP section, which also
    drops constants that only look like addresses."""
    us, jp = cm.us, cm.jp
    us_data = [(p.us_start, p.us_end) for p in sections if p.name != ".text"]
    jp_all = [(p.jp_start, p.jp_end) for p in sections]
    # Sections that moved cannot hold an object at an unchanged address, so an
    # identical pair there is a constant (bit pattern, float) that looks like one.
    moved = [(p.us_start, p.us_end) for p in sections
             if p.name != ".text" and p.jp_start != p.us_start]
    for i, j in cm.aligned_pairs():
        a = us.refs.get(i)
        if a is None or a[0] not in (REF_ABS, REF_GP):
            continue
        b = jp.refs.get(j)
        if b is None or b[0] != a[0]:
            continue
        if not (_inside(us_data, a[1]) and _inside(jp_all, b[1])):
            continue
        if a[1] == b[1] and _inside(moved, a[1]):
            continue
        yield a[1], b[1]


def collect_evidence(cm, sections: List[SecPair]) -> Dict[Tuple[int, int], int]:
    """{(us_target, jp_target): number of code sites}."""
    return dict(Counter(evidence_pairs(cm, sections)))


def make_same_word(code_translate: Callable[[int], Optional[int]],
                   bounds: Tuple[int, int]) -> Callable[[int, int], bool]:
    """Word comparison for the content walk. Code pointers follow the code map;
    other in-image values (data pointers, code pointers into regions the code
    map does not cover) only have to stay close, since data shifts by a small
    amount."""
    lo, hi = bounds

    def same_word(wu: int, wj: int) -> bool:
        if wu == wj:
            return True
        if not (lo <= wu < hi and lo <= wj < hi):
            return False
        t = code_translate(wu)
        if t is not None:
            return t == wj
        return abs(wj - wu) <= POINTER_SLACK

    return same_word


def _nearest(lst: List[int], center: int, window: int, limit: int) -> List[int]:
    """Up to `limit` entries of the sorted list within `window` of center,
    nearest first."""
    mid = bisect.bisect_left(lst, center)
    left, right = mid - 1, mid
    out = []
    while len(out) < limit:
        lv = lst[left] if left >= 0 and center - lst[left] <= window else None
        rv = lst[right] if right < len(lst) and lst[right] - center <= window else None
        if lv is None and rv is None:
            break
        if rv is None or (lv is not None and center - lv <= rv - center):
            out.append(lv)
            left -= 1
        else:
            out.append(rv)
            right += 1
    return out


def walk_words(uw: List[int], jw: List[int], us_start: int, jp_start: int, name: str,
               same_word: Callable[[int, int], bool], bounds: Tuple[int, int]
               ) -> Tuple[List[Range], List[Uncertain], dict]:
    """Content walk over one section's words. The running shift `sh` is the JP
    word index minus the US word index of corresponding words."""
    if (jp_start - us_start) & 3:
        raise ValueError("section %s start delta is not word aligned" % name)
    lo, hi = bounds
    un, jn = len(uw), len(jw)
    scan = RESYNC_SCAN >> 2
    window = RESYNC_WINDOW >> 2

    # Non-zero JP words by value: string anchors are found through this index.
    index: Dict[int, List[int]] = {}
    for k, w in enumerate(jw):
        if w:
            index.setdefault(w, []).append(k)

    def verify(q: int, sh: int) -> bool:
        j0 = q + sh
        if j0 < 0:
            return False
        # Shorter windows are accepted only where a section ends.
        n = min(RESYNC_WORDS, un - q, jn - j0)
        if n < 2:
            return False
        nonzero = 0
        for k in range(n):
            u = uw[q + k]
            if not same_word(u, jw[j0 + k]):
                return False
            if u:
                nonzero += 1
        # Zero runs match at any shift, so they cannot vouch for a delta.
        return nonzero >= 2

    def mostly_same(q: int, sh: int) -> bool:
        """Dense in-place edits (a record table with one changed field per
        record) never give RESYNC_WORDS consecutive hits, and would otherwise
        resync at a shift by whole records. Most non-zero words agreeing at the
        current shift means the content did not move."""
        j0 = q + sh
        n = min(DENSE_WORDS, un - q, jn - j0)
        if j0 < 0 or n < 1:
            return False
        nonzero = hits = 0
        for k in range(n):
            u = uw[q + k]
            if u:
                nonzero += 1
                if same_word(u, jw[j0 + k]):
                    hits += 1
        return nonzero >= DENSE_MIN_NONZERO and hits * DENSE_DEN >= nonzero * DENSE_NUM

    def resync(pos: int, sh: int) -> Optional[Tuple[int, int, bool]]:
        """(position, shift, dense) of the earliest resync point; dense marks
        an in-place acceptance that no strict window vouches for."""
        for q in range(pos, min(pos + scan, un)):
            if verify(q, sh):
                return q, sh, False
            if q > pos and mostly_same(q, sh):
                return q, sh, True
            u = uw[q]
            # Pointers change value with the shift, so only plain words index.
            if u and not lo <= u < hi:
                for k in _nearest(index.get(u, ()), q + sh, window, MAX_CANDIDATES):
                    if k - q != sh and verify(q, k - q):
                        return q, k - q, False
        return None

    def addr(p: int) -> int:
        return us_start + 4 * p

    def delta(sh: int) -> int:
        return 4 * sh + jp_start - us_start

    ranges: List[Range] = []
    unc: List[Uncertain] = []
    stats = {"inplace_words": 0, "inplace_spans": [], "resyncs": 0, "no_resync": 0}
    spans = stats["inplace_spans"]

    # Inside a dense stretch the shift is only a best guess, so its rows are
    # medium until a strict window agrees again.
    dense = False

    def close(run: int, pos: int, sh: int) -> None:
        if pos > run:
            ranges.append(Range(addr(run), addr(pos), delta(sh), name, "content",
                                "medium" if dense else "high"))

    sh = pos = run = 0
    while pos < un:
        if dense and verify(pos, sh):
            close(run, pos, sh)
            dense, run = False, pos
        j = pos + sh
        if 0 <= j < jn:
            if same_word(uw[pos], jw[j]):
                pos += 1
                continue
        else:
            close(run, pos, sh)
            unc.append(Uncertain(addr(pos), addr(un), name, [delta(sh)], "beyond JP section"))
            pos = run = un
            dense = False
            break
        hit = resync(pos, sh)
        if hit is None:
            close(run, pos, sh)
            stop = min(pos + scan, un)
            unc.append(Uncertain(addr(pos), addr(stop), name, [delta(sh)], "no resync"))
            stats["no_resync"] += 1
            pos = run = stop
            dense = False
            continue
        q, c, is_dense = hit
        if c == sh:
            # Same length, changed content (constant, text of equal size).
            # Count only words that really differ; zero runs are accepted
            # without proof but are not edits.
            stats["inplace_words"] += sum(1 for k in range(pos, q)
                                          if not same_word(uw[k], jw[k + sh]))
            if spans and spans[-1][1] == addr(pos):
                spans[-1][1] = addr(q)
            else:
                spans.append([addr(pos), addr(q)])
            if is_dense and not dense:
                close(run, pos, sh)
                dense, run = True, pos
            pos = q
            continue
        stats["resyncs"] += 1
        close(run, pos, sh)
        dense = False
        if q > pos:
            unc.append(Uncertain(addr(pos), addr(q), name, [delta(sh), delta(c)],
                                 "content differs"))
        run = pos = q
        sh = c
    close(run, pos, sh)
    return merge_ranges(ranges), unc, stats


def map_progbits(us_elf, jp_elf, sec: SecPair, code_translate, image_bounds,
                 log=print) -> Tuple[List[Range], List[Uncertain], dict]:
    """Map one PROGBITS section by comparing its content in both ELFs."""
    uw = words(us_elf, sec.us_start, sec.us_start + ((sec.us_end - sec.us_start) & ~3))
    jw = words(jp_elf, sec.jp_start, sec.jp_start + ((sec.jp_end - sec.jp_start) & ~3))
    same_word = make_same_word(code_translate, image_bounds)
    ranges, unc, stats = walk_words(uw, jw, sec.us_start, sec.jp_start, sec.name,
                                    same_word, image_bounds)
    # Section sizes need not be word multiples (.sdata): compare the leftover
    # bytes directly.
    tail = sec.us_start + 4 * len(uw)
    if tail < sec.us_end:
        n = sec.us_end - tail
        last = ranges[-1] if ranges and ranges[-1].us_end == tail else None
        jt = tail + last.delta if last else 0
        if last and jt + n <= sec.jp_end and us_elf.read(tail, n) == jp_elf.read(jt, n):
            ranges[-1] = replace(last, us_end=sec.us_end)
        else:
            unc.append(Uncertain(tail, sec.us_end, sec.name,
                                 [last.delta] if last else [], "partial word"))
    stats["uncertain_bytes"] = sum(u.us_end - u.us_start for u in unc)
    stats["deltas"] = sorted({r.delta for r in ranges})
    stats["medium_ranges"] = sum(1 for r in ranges if r.confidence == "medium")
    log("data %s: %d ranges, %d uncertain (%d bytes), inplace %d words, deltas [%s]"
        % (sec.name, len(ranges), len(unc), stats["uncertain_bytes"],
           stats["inplace_words"], ", ".join("%+#x" % d for d in stats["deltas"])))
    return ranges, unc, stats
