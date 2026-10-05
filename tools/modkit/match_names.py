import argparse
import difflib
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "modkit"

TIERS = ("member", "hash", "size", "position", "global")


def read_listing(path):
    """Parse `vfs_test files` output into a list of members.

    Each member is (nfiles, slots); a slot is (size, hash) or None for an
    absent slot. nfiles is -1 for a member that did not decode (no slots).
    """
    members = []
    count = None
    with open(path, "r", encoding="utf-8") as fp:
        for ln, line in enumerate(fp, 1):
            f = line.split()
            if not f:
                continue
            if f[0] == "count":
                count = int(f[1])
            elif f[0] == "member":
                m, n = int(f[1]), int(f[2])
                if m != len(members):
                    raise ValueError("%s:%d: member %d out of order" % (path, ln, m))
                members.append((n, [None] * max(n, 0)))
            elif f[0] == "file":
                m, k, size, h = int(f[1]), int(f[2]), int(f[3]), f[4]
                nfiles, slots = members[m]
                if not 0 <= k < nfiles:
                    raise ValueError("%s:%d: slot %d/%d out of range" % (path, ln, m, k))
                slots[k] = None if h == "-" else (size, h)
            else:
                raise ValueError("%s:%d: unknown record %r" % (path, ln, f[0]))
    if count is not None and count != len(members):
        raise ValueError("%s: count %d but %d members" % (path, count, len(members)))
    return members


def read_names(path):
    names = {}
    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m, k, name = line.split(" ", 2)
            names[(int(m), int(k))] = name
    return names


def signature(member):
    nfiles, slots = member
    return (nfiles, tuple(slots))


def align(us, jp):
    """Return (exact, structural): dicts JP member -> US member."""
    us_sigs = [signature(x) for x in us]
    jp_sigs = [signature(x) for x in jp]
    exact, structural = {}, {}
    sm = difflib.SequenceMatcher(None, us_sigs, jp_sigs, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for d in range(i2 - i1):
                exact[j1 + d] = i1 + d
        elif tag == "replace":
            cursor = i1
            for j in range(j1, j2):
                nfiles, slots = jp[j]
                if nfiles <= 0:
                    continue
                pattern = tuple(s is None for s in slots)
                for i in range(cursor, i2):
                    unf, uslots = us[i]
                    if unf == nfiles and tuple(s is None for s in uslots) == pattern:
                        structural[j] = i
                        cursor = i + 1
                        break
    return exact, structural


def match(us, jp, us_names, structure_fallback=True):
    """Return (assign, ambiguous).

    assign maps (jp_m, jp_k) -> (tier, us_m, us_k, name); ambiguous is the
    set of present JP slots whose hash maps to several different US names.
    """
    assign = {}
    exact, structural = align(us, jp)

    for j, i in sorted(exact.items()):
        for k, slot in enumerate(jp[j][1]):
            name = us_names.get((i, k))
            if slot is not None and name is not None:
                assign[(j, k)] = ("member", i, k, name)

    for j, i in sorted(structural.items()):
        uslots = us[i][1]
        by_hash = {}
        for uk, s in enumerate(uslots):
            if s is not None and (i, uk) in us_names:
                by_hash.setdefault(s, []).append(uk)
        for k, slot in enumerate(jp[j][1]):
            if slot is None:
                continue
            ks = by_hash.get(slot)
            if ks:
                uk = k if k in ks else ks[0]
                assign[(j, k)] = ("hash", i, uk, us_names[(i, uk)])
                continue
            us_slot = uslots[k]
            if us_slot is None or (i, k) not in us_names:
                continue
            if us_slot[0] == slot[0]:
                assign[(j, k)] = ("size", i, k, us_names[(i, k)])
            elif structure_fallback:
                assign[(j, k)] = ("position", i, k, us_names[(i, k)])

    # Hash -> every (us_m, us_k, name) carrying it, over all US members.
    index = {}
    for i, (_, uslots) in enumerate(us):
        for uk, s in enumerate(uslots):
            if s is not None and (i, uk) in us_names:
                index.setdefault(s, []).append((i, uk, us_names[(i, uk)]))

    ambiguous = set()
    for j, (_, slots) in enumerate(jp):
        for k, slot in enumerate(slots):
            if slot is None or (j, k) in assign:
                continue
            hits = index.get(slot)
            if not hits:
                continue
            if len({h[2] for h in hits}) == 1:
                assign[(j, k)] = ("global",) + hits[0]
            else:
                ambiguous.add((j, k))
    return assign, ambiguous


def main(argv=None):
    ap = argparse.ArgumentParser(prog="modkit.match_names")
    ap.add_argument("--us-list", required=True, help="vfs_test files listing of the US disc")
    ap.add_argument("--jp-list", required=True, help="vfs_test files listing of the JP disc")
    ap.add_argument("--us-names", required=True, help="US pac_names.txt")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--report", help="write a per-slot TSV for spot checks")
    ap.add_argument("--no-structure-fallback", action="store_true",
                    help="do not name by slot position when size and hash differ")
    args = ap.parse_args(argv)

    us = read_listing(args.us_list)
    jp = read_listing(args.jp_list)
    us_names = read_names(args.us_names)
    assign, ambiguous = match(us, jp, us_names, not args.no_structure_fallback)

    present = sum(1 for _, slots in jp for s in slots if s is not None)
    counts = dict.fromkeys(TIERS, 0)
    for tier, *_ in assign.values():
        counts[tier] += 1
    named_members = len({m for m, _ in assign})
    total = len(jp)
    summary = [
        "%d of %d members carry names, %d of %d present files named."
        % (named_members, total, len(assign), present),
        "by tier: " + ", ".join("%s %d" % (t, counts[t]) for t in TIERS)
        + "; ambiguous %d" % len(ambiguous),
    ]

    with open(args.out, "w", encoding="utf-8", newline="\n") as fp:
        fp.write("# DATA.PAC name table for SLPS-25418, generated by modkit.match_names\n")
        fp.write("# from the SLUS-20851 table by matching file contents.\n")
        fp.write("# <member> <index> <name>\n")
        fp.write("# members: %d\n" % total)
        for line in summary:
            fp.write("# " + line + "\n")
        for (m, k) in sorted(assign):
            fp.write("%d %d %s\n" % (m, k, assign[(m, k)][3]))

    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fp:
            fp.write("m\tk\tsize\ttier\tus_m\tus_k\tname\n")
            for m, (_, slots) in enumerate(jp):
                for k, slot in enumerate(slots):
                    if slot is None:
                        continue
                    a = assign.get((m, k))
                    if a:
                        rest = "%s\t%d\t%d\t%s" % a
                    elif (m, k) in ambiguous:
                        rest = "ambiguous\t\t\t"
                    else:
                        rest = "\t\t\t"
                    fp.write("%d\t%d\t%d\t%s\n" % (m, k, slot[0], rest))

    print("wrote %s" % args.out)
    for line in summary:
        print(line)
    print("unnamed %d (ambiguous %d)" % (present - len(assign), len(ambiguous)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
