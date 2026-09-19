"""Minimal ISO9660 reader for PS2 disc images.

Usage:
    python tools/iso9660.py <image.iso> ls [dir]
    python tools/iso9660.py <image.iso> cat <path> [-o out]
    python tools/iso9660.py <image.iso> x <path> [<path> ...] -d <outdir>

Paths are case-insensitive and the ';1' version suffix is optional.
Only the primary volume descriptor is read; PS2 discs have no Joliet.
"""

import argparse
import os
import struct
import sys

SECTOR = 2048


class Entry:
    __slots__ = ("name", "lba", "size", "is_dir")

    def __init__(self, name, lba, size, is_dir):
        self.name, self.lba, self.size, self.is_dir = name, lba, size, is_dir


class Iso:
    def __init__(self, path):
        self.f = open(path, "rb")
        pvd = self._read(16, SECTOR)
        if pvd[1:6] != b"CD001" or pvd[0] != 1:
            raise SystemExit("%s: no ISO9660 primary volume descriptor" % path)
        self.volume_id = pvd[40:72].decode("ascii", "replace").strip()
        self.root = self._parse_record(pvd[156:190])

    def _read(self, lba, size):
        self.f.seek(lba * SECTOR)
        return self.f.read(size)

    @staticmethod
    def _parse_record(rec):
        lba = struct.unpack_from("<I", rec, 2)[0]
        size = struct.unpack_from("<I", rec, 10)[0]
        flags = rec[25]
        nlen = rec[32]
        raw = rec[33:33 + nlen]
        if raw == b"\x00":
            name = "."
        elif raw == b"\x01":
            name = ".."
        else:
            name = raw.decode("ascii", "replace")
        return Entry(name, lba, size, bool(flags & 2))

    def listdir(self, d):
        data = self._read(d.lba, d.size)
        out = []
        pos = 0
        while pos < len(data):
            rlen = data[pos]
            if rlen == 0:
                # Records never straddle sectors; skip the padding.
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            e = self._parse_record(data[pos:pos + rlen])
            if e.name not in (".", ".."):
                out.append(e)
            pos += rlen
        return out

    def lookup(self, path):
        cur = self.root
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            if not cur.is_dir:
                raise KeyError(path)
            want = part.upper().split(";")[0]
            for e in self.listdir(cur):
                if e.name.upper().split(";")[0] == want:
                    cur = e
                    break
            else:
                raise KeyError(path)
        return cur

    def read(self, e):
        return self._read(e.lba, e.size)

    def walk(self, d=None, prefix=""):
        d = d or self.root
        for e in self.listdir(d):
            p = prefix + "/" + e.name.split(";")[0]
            yield p, e
            if e.is_dir:
                yield from self.walk(e, p)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("iso")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ls", help="list a directory (-r for the whole tree)")
    p.add_argument("dir", nargs="?", default="")
    p.add_argument("-r", action="store_true")
    p = sub.add_parser("cat", help="write one file to stdout or -o")
    p.add_argument("path")
    p.add_argument("-o")
    p = sub.add_parser("x", help="extract files into a directory")
    p.add_argument("paths", nargs="+")
    p.add_argument("-d", required=True)
    a = ap.parse_args()

    iso = Iso(a.iso)
    try:
        if a.cmd == "ls":
            d = iso.lookup(a.dir)
            items = iso.walk(d, a.dir.rstrip("/")) if a.r else \
                ((e.name, e) for e in iso.listdir(d))
            for name, e in items:
                kind = "d" if e.is_dir else "-"
                print("%s %10d  lba=%-8d %s" % (kind, e.size, e.lba, name))
        elif a.cmd == "cat":
            data = iso.read(iso.lookup(a.path))
            if a.o:
                with open(a.o, "wb") as f:
                    f.write(data)
            else:
                sys.stdout.buffer.write(data)
        elif a.cmd == "x":
            os.makedirs(a.d, exist_ok=True)
            for path in a.paths:
                e = iso.lookup(path)
                dst = os.path.join(a.d, os.path.basename(path.split(";")[0]))
                with open(dst, "wb") as f:
                    f.write(iso.read(e))
                print("%s -> %s (%d bytes)" % (path, dst, e.size))
    except KeyError as ex:
        raise SystemExit("not found on disc: %s" % ex.args[0])


if __name__ == "__main__":
    main()
