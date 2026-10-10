#!/usr/bin/env python3
"""Build a test texture pack from a texture dump (PS2_TEX_DUMP output).

A dump holds <key16hex>_<w>x<h>.png files plus an index.csv. A pack uses the
same names; the image inside may be K times larger in both axes.

Modes:
  copy     K must be 1; files are copied byte for byte.
  nearest  nearest-neighbour K x upscale (renders like the original with
           nearest filtering).
  marker   K >= 2. Nearest upscale, then every pixel with alpha > 0 and
           (x - y) % K == 0 inside the border becomes red (alpha kept), and
           the outermost ring of pixels becomes opaque magenta. The ring shows
           where the sprite sits at one hi-res texel width, the diagonal shows
           detail below the native pixel size.

Usage:
  texpack_testpack.py --src DIR [--src DIR ...] --out DIR --mode MODE
                      --scale K [--psm P] [--size WxH]
"""
import argparse
import csv
import os
import re
import shutil
import sys

import numpy as np
from PIL import Image

NAME_RE = re.compile(r"^([0-9a-fA-F]{16})_(\d+)x(\d+)\.png$", re.IGNORECASE)

MAGENTA = (255, 0, 255, 255)
RED = (255, 0, 0)


def read_psm_index(src):
    """Return {(key, w, h): set of psm} from src/index.csv, or None."""
    path = os.path.join(src, "index.csv")
    if not os.path.isfile(path):
        return None
    out = {}
    with open(path, newline="") as fp:
        for row in csv.DictReader(fp):
            k = (row["key"].lower(), int(row["w"]), int(row["h"]))
            out.setdefault(k, set()).add(int(row["psm"]))
    return out


def make_marker(src_rgba, k):
    hi = np.repeat(np.repeat(src_rgba, k, axis=0), k, axis=1).copy()
    h, w = hi.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    diag = (hi[:, :, 3] > 0) & (((xx - yy) % k) == 0)
    diag[0, :] = diag[-1, :] = False
    diag[:, 0] = diag[:, -1] = False
    hi[diag, 0:3] = RED
    hi[0, :] = hi[-1, :] = MAGENTA
    hi[:, 0] = hi[:, -1] = MAGENTA
    return hi


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", action="append", required=True,
                    help="dump directory (repeatable; the first one holding a "
                    "name wins)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("copy", "nearest", "marker"), required=True)
    ap.add_argument("--scale", type=int, required=True)
    ap.add_argument("--psm", type=int, default=None,
                    help="keep only textures with this GS pixel format "
                    "(needs index.csv in the src)")
    ap.add_argument("--size", default=None, help="keep only textures of WxH")
    a = ap.parse_args()

    if not 1 <= a.scale <= 8:
        sys.exit("--scale must be 1..8")
    if a.mode == "copy" and a.scale != 1:
        sys.exit("--mode copy needs --scale 1")
    if a.mode == "marker" and a.scale < 2:
        sys.exit("--mode marker needs --scale >= 2")
    want_size = None
    if a.size:
        m = re.fullmatch(r"(\d+)x(\d+)", a.size)
        if not m:
            sys.exit("--size must look like 32x32")
        want_size = (int(m.group(1)), int(m.group(2)))

    os.makedirs(a.out, exist_ok=True)
    # Drop pack files of an earlier run so the result matches this command only.
    for n in os.listdir(a.out):
        if NAME_RE.match(n):
            os.remove(os.path.join(a.out, n))

    seen = set()
    written = 0
    for src in a.src:
        if not os.path.isdir(src):
            sys.exit("not a directory: %s" % src)
        index = read_psm_index(src) if a.psm is not None else None
        if a.psm is not None and index is None:
            sys.exit("--psm needs %s" % os.path.join(src, "index.csv"))
        for n in sorted(os.listdir(src)):
            m = NAME_RE.match(n)
            if not m:
                continue
            key, w, h = m.group(1).lower(), int(m.group(2)), int(m.group(3))
            if want_size and (w, h) != want_size:
                continue
            if a.psm is not None and a.psm not in index.get((key, w, h), ()):
                continue
            name = "%s_%dx%d.png" % (key, w, h)
            if name in seen:
                continue
            seen.add(name)
            dst = os.path.join(a.out, name)
            sp = os.path.join(src, n)
            if a.mode == "copy":
                shutil.copyfile(sp, dst)
            else:
                with Image.open(sp) as im:
                    px = np.asarray(im.convert("RGBA"))
                if px.shape[1] != w or px.shape[0] != h:
                    sys.exit("%s: image is %dx%d, name says %dx%d"
                             % (sp, px.shape[1], px.shape[0], w, h))
                if a.mode == "nearest":
                    hi = np.repeat(np.repeat(px, a.scale, axis=0), a.scale, axis=1)
                else:
                    hi = make_marker(px, a.scale)
                Image.fromarray(np.ascontiguousarray(hi), "RGBA").save(dst)
            written += 1

    print("%d files written to %s" % (written, a.out))


if __name__ == "__main__":
    main()
