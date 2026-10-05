import os
import shutil
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

from modkit import match_names


def listing(path, members):
    """members: list of slot lists; a slot is (size, hash) or None."""
    with open(path, "w", newline="\n") as fp:
        fp.write("count %d\n" % len(members))
        for m, slots in enumerate(members):
            fp.write("member %d %d 0\n" % (m, len(slots)))
            for k, s in enumerate(slots):
                if s is None:
                    fp.write("file %d %d 0 -\n" % (m, k))
                else:
                    fp.write("file %d %d %d %s\n" % (m, k, s[0], s[1]))


def h(n):
    return "%024x" % n


def read_table(path):
    comments, rows = [], {}
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.rstrip("\n")
            if line.startswith("#"):
                comments.append(line)
            else:
                m, k, name = line.split(" ", 2)
                rows[(int(m), int(k))] = name
    return comments, rows


def run(tmp, extra=()):
    out = os.path.join(tmp, "jp_names.txt")
    rep = os.path.join(tmp, "report.tsv")
    rc = match_names.main([
        "--us-list", os.path.join(tmp, "us.txt"),
        "--jp-list", os.path.join(tmp, "jp.txt"),
        "--us-names", os.path.join(tmp, "names.txt"),
        "-o", out, "--report", rep] + list(extra))
    assert rc == 0
    return out, rep


def read_report(path):
    tiers = {}
    with open(path, encoding="utf-8") as fp:
        next(fp)
        for line in fp:
            f = line.rstrip("\n").split("\t")
            tiers[(int(f[0]), int(f[1]))] = f[3]
    return tiers


def main():
    tmp = tempfile.mkdtemp(prefix="match_names_")
    try:
        # US: 0 identical, 1 one changed file (same size), 2 one resized file,
        # 3 identical (follows the JP-only inserts), 4 has an absent slot,
        # 5 and 6 share a hash under different names.
        us = [
            [(10, h(1)), (20, h(2)), (30, h(3))],
            [(11, h(4)), (21, h(5))],
            [(12, h(6)), (22, h(7)), (32, h(8))],
            [(13, h(9)), (23, h(10))],
            [(14, h(11)), None, (34, h(12))],
            [(15, h(13))],
            [(15, h(13))],
        ]
        names = {}
        for m, slots in enumerate(us):
            for k, s in enumerate(slots):
                if s is not None:
                    names[(m, k)] = "dir%d/file%d.bin" % (m, k)
        names[(6, 0)] = "dir6/other.bin"
        # Member 7 holds a single file the table does not name.
        us.append([(16, h(14))])

        jp = [
            us[0],
            [(99, h(100)), (98, h(101)), (97, h(102)), (96, h(103)), us[3][1]],
            [us[1][0], (21, h(50))],
            [us[2][0], (23, h(51)), (32, h(8))],
            us[3],
            us[4],
            [(15, h(13)), None],
        ]
        # JP slot with an unnamed US twin stays unnamed.
        jp.append([(16, h(14))])

        listing(os.path.join(tmp, "us.txt"), us)
        listing(os.path.join(tmp, "jp.txt"), jp)
        with open(os.path.join(tmp, "names.txt"), "w", newline="\n") as fp:
            fp.write("# synthetic\n")
            for (m, k), n in sorted(names.items()):
                fp.write("%d %d %s\n" % (m, k, n))

        out, rep = run(tmp)
        comments, rows = read_table(out)
        tiers = read_report(rep)
        print("checks (default):")

        assert "# members: %d" % len(jp) in comments, comments
        print("  header carries '# members: %d'" % len(jp))

        for k in range(3):
            assert tiers[(0, k)] == "member" and rows[(0, k)] == names[(0, k)]
        print("  identical member named by tier member")

        assert all((1, k) not in rows for k in range(4)), "inserted member named"
        assert tiers[(1, 4)] == "global" and rows[(1, 4)] == "dir3/file1.bin"
        assert tiers[(4, 0)] == "member" and rows[(4, 1)] == "dir3/file1.bin"
        print("  JP-only member named only by global hash; later member still pairs by content")

        assert tiers[(2, 0)] == "hash" and rows[(2, 0)] == "dir1/file0.bin"
        assert tiers[(2, 1)] == "size" and rows[(2, 1)] == "dir1/file1.bin"
        assert tiers[(3, 0)] == "hash" and rows[(3, 2)] == "dir2/file2.bin"
        assert tiers[(3, 1)] == "position" and rows[(3, 1)] == "dir2/file1.bin"
        print("  changed files named by hash, size and position")

        assert (5, 1) not in rows and (5, 1) not in tiers
        assert rows[(5, 0)] == "dir4/file0.bin" and rows[(5, 2)] == "dir4/file2.bin"
        print("  absent slots get no line")

        assert (6, 0) not in rows and tiers[(6, 0)] == "ambiguous"
        assert any("ambiguous 1" in c for c in comments), comments
        print("  hash shared by two US names is ambiguous and unnamed")

        assert (7, 0) not in rows
        print("  slot whose US twin is unnamed stays unnamed")

        assert all(m < len(jp) and jp[m][k] is not None for (m, k) in rows)
        assert list(rows) == sorted(rows)
        print("  only present slots, sorted by member and index")

        out2, rep2 = run(tmp, ["--no-structure-fallback"])
        _, rows2 = read_table(out2)
        assert (3, 1) not in rows2 and (2, 1) in rows2
        assert set(rows) - set(rows2) == {(3, 1)}
        print("  --no-structure-fallback drops only the position name")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS: match_names")


main()
