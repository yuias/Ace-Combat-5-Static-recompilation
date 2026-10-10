import argparse
import os
import subprocess
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def replay(build, capture, fields, shot, env_extra):
    exe = os.path.join(ROOT, build, "gsreplay.exe")
    env = dict(os.environ)
    env["PS2_SHADER_DIR"] = os.path.join(ROOT, build, "shaders")
    env["PS2_PRESENT_MODE"] = "immediate"
    env.update(env_extra)
    log = shot.replace(".ppm", ".log")
    with open(log, "w") as fp:
        r = subprocess.run([exe, capture, "--loop", "1", "--fields", str(fields),
                            "--shot", shot], cwd=ROOT, env=env, stdout=fp,
                           stderr=subprocess.STDOUT)
    if r.returncode != 0 or not os.path.exists(shot):
        sys.exit("gsreplay failed for %s (see %s)" % (shot, log))
    return np.asarray(Image.open(shot).convert("RGB")).astype(np.int16), log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--fields", type=int, nargs="+", required=True)
    ap.add_argument("--switch", default="PS2_RN_SUN")
    ap.add_argument("--out", default=os.path.join("out", "rn", "compare"))
    ap.add_argument("--env", action="append", default=[],
                    help="extra NAME=VALUE for both runs")
    ap.add_argument("--build", default=os.path.join("build", "clang"),
                    help="build directory holding gsreplay.exe and shaders/")
    ap.add_argument("--base", default=None,
                    help="directory with gsreplay.exe and shaders/ used for "
                    "run A instead of --build")
    ap.add_argument("--values", nargs=2, default=["0", "1"], metavar=("A", "B"),
                    help="switch values for run A and run B")
    ap.add_argument("--tolerance", type=int, default=2,
                    help="a pixel differs when a channel differs by more than this")
    ap.add_argument("--expect-identical", action="store_true",
                    help="exit 1 when any field differs or picture sizes differ")
    args = ap.parse_args()
    out = os.path.join(ROOT, args.out)
    os.makedirs(out, exist_ok=True)
    extra = dict(e.split("=", 1) for e in args.env)
    worst = 0
    mismatch = False
    build_a = args.base if args.base else args.build
    for f in args.fields:
        a, la = replay(build_a, args.capture, f,
                       os.path.join(out, "f%04d_emulated.ppm" % f),
                       dict(extra, **{args.switch: args.values[0]}))
        b, lb = replay(args.build, args.capture, f,
                       os.path.join(out, "f%04d_native.ppm" % f),
                       dict(extra, **{args.switch: args.values[1]}))
        if a.shape != b.shape:
            print("field %d: picture sizes differ %s vs %s" % (f, a.shape, b.shape))
            mismatch = True
            continue
        d = np.abs(a - b)
        mask = d.max(axis=2) > args.tolerance
        n = int(mask.sum())
        Image.fromarray(np.clip(d * 4, 0, 255).astype(np.uint8)).save(
            os.path.join(out, "f%04d_diff.png" % f))
        sheet = np.concatenate([a, b, np.clip(d * 4, 0, 255)], axis=1)
        Image.fromarray(sheet.astype(np.uint8)).save(
            os.path.join(out, "f%04d_sheet.png" % f))
        claimed = native = "?"
        for line in open(lb, errors="replace"):
            if "native sun --" in line:
                claimed = line.strip().split("rn: ", 1)[-1]
            if "native draws (rn.h claims)" in line:
                native = line.strip().split("vk: ", 1)[-1]
        if n:
            ys, xs = np.nonzero(mask)
            print("field %4d: %6d pixels differ in x %d..%d y %d..%d, mean %.1f "
                  "max %d" % (f, n, xs.min(), xs.max(), ys.min(), ys.max(),
                              float(d[mask].mean()), int(d.max())))
        else:
            print("field %4d: identical" % f)
        print("            %s" % claimed)
        print("            %s" % native)
        worst = max(worst, n)
    print("sheets (emulated | native | difference x4) in %s" % out)
    if args.expect_identical and (mismatch or worst):
        sys.exit(1)


if __name__ == "__main__":
    main()
