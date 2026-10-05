import argparse
import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "regionmap"

import paths
from ps2recomp.elf import ElfFile

from .common import file_info, pair_sections
from .mapfile import Range, RegionMap, Uncertain


def _load_elf(path, label):
    elf = ElfFile(path)
    if not elf.sections:
        raise SystemExit("%s ELF %s has no section headers; regionmap needs them "
                         "to pair sections" % (label, os.path.basename(path)))
    return elf


def _section_dict(p):
    return {"name": p.name, "us_start": p.us_start, "us_end": p.us_end,
            "jp_start": p.jp_start, "jp_end": p.jp_end, "nobits": p.nobits}


def cmd_build(args, log=print):
    t0 = time.time()
    us = _load_elf(args.us_elf, "US")
    jp = _load_elf(args.jp_elf, "JP")
    pairs, only = pair_sections(us, jp)

    log("%-18s %-17s %-17s %8s %8s %8s"
        % ("section", "us", "jp", "delta", "size_us", "size_jp"))
    for p in pairs:
        log("%-18s %08X-%08X %08X-%08X %+#8x %8X %8X"
            % (p.name, p.us_start, p.us_end, p.jp_start, p.jp_end,
               p.jp_start - p.us_start, p.us_end - p.us_start, p.jp_end - p.jp_start))
    if only:
        log("sections in only one ELF: %s" % ", ".join(only))

    # Placeholder map: each section shifts uniformly by its start delta. Later
    # stages replace these rows with evidence-based ranges.
    ranges, uncertain = [], []
    for p in pairs:
        delta = p.jp_start - p.us_start
        common = min(p.us_end - p.us_start, p.jp_end - p.jp_start)
        ranges.append(Range(p.us_start, p.us_start + common, delta, p.name,
                            "section", "low"))
        if p.us_start + common < p.us_end:
            uncertain.append(Uncertain(p.us_start + common, p.us_end, p.name,
                                       [delta], "size differs"))

    rmap = RegionMap(file_info(us), file_info(jp), [_section_dict(p) for p in pairs],
                     ranges, uncertain, [], {})
    rmap.validate()
    path = os.path.join(args.out, "regionmap.json")
    rmap.save(path)
    log("wrote %s" % path)
    log("elapsed %.1f s" % (time.time() - t0))
    return 0


def _parse_addr(text):
    # Always hex: bare digits like 446100 are addresses, not decimal.
    return int(text, 16)


def format_lookup(rmap, addr):
    r = rmap.range_for(addr)
    hit = rmap.function_chunk_at(addr)
    if r is None:
        u = rmap.uncertain_for(addr)
        sec = u.section if u else (rmap.section_name_at(addr) or "?")
        if u:
            detail = "(uncertain: %s" % u.reason
            if u.candidates:
                detail += ", candidates %s" % ",".join("%+#x" % c for c in u.candidates)
            detail += ")"
        else:
            detail = "(no range)"
        return "%08X -> unmapped  %-7s  %s" % (addr, sec, detail)
    line = "%08X -> %08X  %-7s  %s" % (addr, addr + r.delta, r.section,
                                       hit[0].status if hit else r.source)
    if hit:
        line += "  %s+%#x" % (hit[0].name, addr - hit[1])
    return line


def cmd_lookup(args, log=print):
    rmap = RegionMap.load(args.map)
    for text in args.addr:
        log(format_lookup(rmap, _parse_addr(text)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="regionmap",
        description="Map US executable addresses to JP executable addresses.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="compare two executables and write the map")
    b.add_argument("us_elf", metavar="US_ELF")
    b.add_argument("jp_elf", metavar="JP_ELF")
    b.add_argument("--ida", default=paths.config("ida_db.json"),
                   help="US function list (default: config/ida_db.json)")
    b.add_argument("--out", default=os.path.join(paths.ROOT, "tmp", "regionmap"),
                   help="output directory (default: tmp/regionmap)")
    b.add_argument("--heuristic", help="earlier heuristic delta listing to cross-check")
    b.add_argument("--config-dir", default=paths.CONFIG,
                   help="directory with hooks.json and report.json (default: config)")
    b.add_argument("--runtime", default=os.path.join(paths.ROOT, "runtime"),
                   help="runtime source tree scanned for required addresses")
    b.add_argument("--no-anchors", action="store_true",
                   help="skip the required-address check")
    b.set_defaults(func=cmd_build)

    lk = sub.add_parser("lookup", help="translate US addresses with a built map")
    lk.add_argument("map", metavar="MAP_JSON")
    lk.add_argument("addr", metavar="ADDR", nargs="+")
    lk.set_defaults(func=cmd_lookup)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
