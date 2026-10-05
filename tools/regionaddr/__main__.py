import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "regionaddr"

import regions
from regionconfig.__main__ import _load

from . import ListError, gen, lint, parse_list

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _path(*parts):
    return os.path.join(ROOT, *parts)


def _generator_inputs(args):
    """Entries, translator, verdicts and the ida_db function sets."""
    list_path = _path(*lint.LIST_REL.split("/"))
    with open(list_path, encoding="utf-8", newline="") as fp:
        entries = parse_list(fp.read())
    tr = _load(args)
    verdicts = gen.load_verdicts(args.verdicts)
    ida = (gen.load_ida_eas(_path(*regions.US.config_dir.split("/"), "ida_db.json")),
           gen.load_ida_eas(_path(*regions.JP.config_dir.split("/"), "ida_db.json")))
    return entries, tr, verdicts, ida


def cmd_generate(args) -> int:
    entries, tr, verdicts, ida = _generator_inputs(args)
    return gen.run_generate(entries, tr, verdicts, ida, args.jp_out, args.words_out,
                            allow_pending=args.allow_pending)


def cmd_check(args) -> int:
    entries, tr, verdicts, ida = _generator_inputs(args)
    return gen.run_check(entries, tr, verdicts, ida, args.jp_out, args.words_out)


def cmd_show(args) -> int:
    tr = _load(args)
    return gen.run_show(tr, args.us, args.before, args.after, args.jp)


def _hex(text):
    return int(text, 16)


def _add_inputs(p):
    p.add_argument("--map", default=_path("tmp", "regionmap", "regionmap.json"))
    p.add_argument("--us-elf", default=_path("tmp", "us", regions.US.exe_name))
    p.add_argument("--jp-elf", default=_path("tmp", "jp", regions.JP.exe_name))
    p.add_argument("--verdicts", default=_path(*gen.VERDICTS_REL.split("/")))
    p.add_argument("--jp-out", default=_path(*gen.JP_REL.split("/")))
    p.add_argument("--words-out", default=_path(*gen.WORDS_REL.split("/")))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="regionaddr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lint", help="find guest-address literals outside the address list")
    p.add_argument("--strict", action="store_true",
                   help="also fail on literals that still need migrating")
    p = sub.add_parser("generate", help="write the JP address and word tables")
    p.add_argument("--allow-pending", action="store_true",
                   help="write PENDING rows for entries that still need a verdict")
    _add_inputs(p)
    p.set_defaults(func=cmd_generate)
    p = sub.add_parser("check", help="verify the generated tables are current")
    _add_inputs(p)
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("show", help="compare the US and JP instructions around an address")
    p.add_argument("us", type=_hex, help="US address (hex)")
    p.add_argument("--before", type=int, default=8, help="instructions before the address")
    p.add_argument("--after", type=int, default=8, help="instructions after the address")
    p.add_argument("--jp", type=_hex, help="JP address to pair with it instead of the map's")
    _add_inputs(p)
    p.set_defaults(func=cmd_show)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "lint":
            return lint.run(ROOT, strict=args.strict)
        return args.func(args)
    except ListError as e:
        print("regionaddr: %s: %s" % (lint.LIST_REL, e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
