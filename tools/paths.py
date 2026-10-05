import os

import regions

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
PARENT = os.path.dirname(ROOT)

CONFIG = os.path.join(ROOT, "config")
GENERATED = os.path.join(ROOT, "generated")

DISC_NAME = regions.US.disc_name

DISC_CANDIDATES = (os.path.join(PARENT, DISC_NAME),
                   os.path.join(PARENT, "game", DISC_NAME))


def _candidates(region):
    return (os.path.join(PARENT, region.disc_name),
            os.path.join(PARENT, "game", region.disc_name))


def _fixed_region():
    key = os.environ.get("AC5_REGION")
    if not key:
        return None
    try:
        return regions.by_key(key)
    except ValueError as e:
        raise SystemExit("AC5_REGION: %s" % e)


def _find_disc(fixed):
    """Return (disc folder, region whose folder name matched or None)."""
    env = os.environ.get("AC5_DISC")
    if env:
        return env, None
    for region in ((fixed,) if fixed else regions.REGIONS):
        for cand in _candidates(region):
            if os.path.exists(cand):
                return cand, region
    # Nothing found: keep the US error message unless a region was forced.
    return _candidates(fixed or regions.US)[0], None


def _disc_region(disc):
    """Region named by the disc's SYSTEM.CNF, or None."""
    try:
        with open(os.path.join(disc, "SYSTEM.CNF"), "r", errors="replace") as f:
            exe = regions.exe_from_system_cnf(f.read())
    except OSError:
        return None
    return regions.by_exe_name(exe) if exe else None


def _choose():
    fixed = _fixed_region()
    disc, matched = _find_disc(fixed)
    region = fixed or _disc_region(disc) or matched or regions.US
    return region, disc


REGION, DISC = _choose()
GAME = os.path.join(DISC, REGION.exe_name)

PS2SDK = os.environ.get("PS2SDK_DIR") or os.path.join(PARENT, "PS2SDK")
SDK_LIBS = os.path.join(PS2SDK, "sce", "ee", "lib")


def config(name):
    return os.path.join(CONFIG, name)


def region_config(name):
    return regions.config_file(REGION, name, ROOT)


def require(path, what, hint):
    if not os.path.exists(path):
        raise SystemExit("%s not found:\n    %s\n%s" % (what, path, hint))
    return path


DISC_HINT = ("Set AC5_DISC to the extracted disc directory, or put it beside\n"
             "the repo (or under game/) as '%s'.\n"
             "Set AC5_REGION (%s) to choose the region explicitly."
             % (REGION.disc_name, "/".join(r.key for r in regions.REGIONS)))
SDK_HINT = ("Set PS2SDK_DIR to a PS2SDK checkout.  Only the signature matcher\n"
            "needs it, and its output is already in config/sdk_symbols.json --\n"
            "you only need the SDK to regenerate that.")


def game():
    return require(GAME, "The game executable (%s)" % REGION.exe_name, DISC_HINT)


def sdk_libs():
    return require(SDK_LIBS, "The PS2SDK EE libraries", SDK_HINT)
