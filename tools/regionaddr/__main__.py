import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "regionaddr"

from . import ListError, lint

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="regionaddr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lint", help="find guest-address literals outside the address list")
    p.add_argument("--strict", action="store_true",
                   help="also fail on literals that still need migrating")
    args = ap.parse_args(argv)
    try:
        return lint.run(ROOT, strict=args.strict)
    except ListError as e:
        print("regionaddr: %s: %s" % (lint.LIST_REL, e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
