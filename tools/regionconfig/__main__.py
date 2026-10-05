import argparse
import hashlib
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "regionconfig"

import regions
from ps2recomp.elf import ElfFile
from regionmap import RegionMap

from . import symbols
from .core import Translator, display_path, print_failures, run_handlers, totals

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Handlers run in this order; later tasks append.
HANDLERS = [
    symbols.handle_hooks,
    symbols.handle_overrides,
    symbols.handle_manual_symbols,
    symbols.handle_sdk_symbols,
    symbols.handle_game_symbols,
]


def _default(*parts):
    return os.path.join(ROOT, *parts)


def verify_inputs(rmap, us_elf, jp_elf) -> None:
    """The map and the configs are only valid for these exact executables."""
    for label, region, elf, info in (("US", regions.US, us_elf, rmap.us_info),
                                     ("JP", regions.JP, jp_elf, rmap.jp_info)):
        digest = hashlib.sha256(elf.data).hexdigest()
        if digest != region.exe_sha256:
            raise SystemExit("regionconfig: %s ELF %s is not %s (sha256 %s)"
                             % (label, os.path.basename(elf.path), region.game_id, digest))
        if info.get("sha256") != digest:
            raise SystemExit("regionconfig: the map was built from a different %s "
                             "executable (map sha256 %s, ELF %s)"
                             % (label, info.get("sha256"), digest))


def generate(tr: Translator, handlers, src_dir: str, log=print):
    """Run all handlers. Returns the output files, or None after printing the
    failures."""
    outputs = run_handlers(tr, handlers, src_dir)
    if tr.failures:
        print_failures(tr, log)
        log("regionconfig: %d failure(s), nothing written" % len(tr.failures))
        return None
    return outputs


def write_outputs(outputs, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for name, data in outputs.items():
        with open(os.path.join(out_dir, name), "wb") as fp:
            fp.write(data)


def run_translate(tr, handlers, src_dir, out_dir, log=print) -> int:
    outputs = generate(tr, handlers, src_dir, log)
    if outputs is None:
        return 1
    write_outputs(outputs, out_dir)
    for name in sorted(outputs):
        if name == "manifest.json":
            continue
        c = tr.counts.get(name, {"kept": 0, "dropped": 0, "rederived": 0})
        names = tr.copied.get(name, 0)
        log("  %-22s kept %5d (addresses %d, names %d)  dropped %4d  rederived %4d"
            % (name, c["kept"], c["kept"] - names, names, c["dropped"], c["rederived"]))
    f, k, d, r = totals(tr, outputs)
    log("regionconfig: %d files, kept %d, dropped %d, rederived %d -> %s"
        % (f, k, d, r, display_path(out_dir)))
    log("  %d note(s) in manifest.json" % len(tr.notes))
    return 0


def run_check(tr, handlers, src_dir, out_dir, log=print) -> int:
    outputs = generate(tr, handlers, src_dir, log)
    if outputs is None:
        return 1
    bad = False
    for name in sorted(outputs):
        path = os.path.join(out_dir, name)
        have = None
        if os.path.isfile(path):
            with open(path, "rb") as fp:
                have = fp.read()
        if have != outputs[name]:
            log("regionconfig: %s differs" % name)
            bad = True
    if bad:
        return 1
    log("regionconfig: %s is up to date" % display_path(out_dir))
    return 0


def _load(args):
    for label, path in (("map", args.map), ("US ELF", args.us_elf), ("JP ELF", args.jp_elf)):
        if not os.path.isfile(path):
            raise SystemExit("regionconfig: %s not found: %s" % (label, display_path(path)))
    rmap = RegionMap.load(args.map)
    us_elf, jp_elf = ElfFile(args.us_elf), ElfFile(args.jp_elf)
    verify_inputs(rmap, us_elf, jp_elf)
    return Translator(rmap, us_elf, jp_elf)


def cmd_translate(args, log=print) -> int:
    return run_translate(_load(args), HANDLERS, args.src, args.out, log)


def cmd_check(args, log=print) -> int:
    # Outputs are compared in memory, so --out is never written.
    return run_check(_load(args), HANDLERS, args.src, args.out, log)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="regionconfig",
        description="Translate the US address-bound configs to the JP executable.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, text in (("translate", cmd_translate, "write the JP config set"),
                           ("check", cmd_check, "verify the JP config set is current")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--map", default=_default("tmp", "regionmap", "regionmap.json"))
        p.add_argument("--us-elf", default=_default("tmp", "us", regions.US.exe_name))
        p.add_argument("--jp-elf", default=_default("tmp", "jp", regions.JP.exe_name))
        p.add_argument("--src", default=_default(*regions.US.config_dir.split("/")))
        p.add_argument("--out", default=_default(*regions.JP.config_dir.split("/")))
        p.set_defaults(func=fn)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
