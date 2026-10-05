"""Finds guest-address literals in the runtime sources that are not routed
through runtime/include/ps2_addr_list.h."""
import os
import re

from . import parse_list

LIST_REL = "runtime/include/ps2_addr_list.h"
SCAN_DIRS = (("runtime/src", (".c", ".cpp", ".h")), ("runtime/include", (".h",)))

LO, HI = 0x00100000, 0x00500000
_LITERAL = re.compile(r"\b0x([0-9A-Fa-f]{5,8})[uU]?\b")

# (repo-relative file, value, reason): literals in the address range that are
# not game addresses, or are kept on purpose.
ALLOW = (
    ("runtime/src/ps2_core.c", 0x00100000,
     "PS2_TEXT_LO, EE user-space start, not a game address"),
    ("runtime/src/ps2_gs.c", 0x00200000,
     "GS-side base in the movie/blit path, not a guest address"),
    ("runtime/src/ps2_hle_iop.c", 0x00100000, "size limits"),
    ("runtime/src/ps2_kernel.c", 0x00100000, "stack and heap sizes"),
    ("runtime/src/ps2_main.c", 0x00100000,
     "profiler range for ps2_prof_enable (diagnostic, covers both regions' game code)"),
    ("runtime/src/ps2_main.c", 0x00300000,
     "profiler range end, and nufile_selftest scratch buffer chosen by the runtime"),
    ("runtime/src/ps2_main.c", 0x00310000, "nufile_selftest scratch buffer"),
    ("runtime/src/ps2_statecap.c", 0x00100000, "help text example"),
    ("runtime/include/ps2_runtime.h", 0x00200000, "IOP RAM size"),
    ("runtime/include/ps2_runtime.h", 0x00400000, "BIOS size"),
    ("runtime/include/ac5mod.h", 0x0011DB30,
     "doc comment on mod addresses (mods are out of scope)"),
)

ALLOWED, PENDING, UNKNOWN = "allowed", "pending migration", "unknown"


def _sources(root):
    for rel_dir, exts in SCAN_DIRS:
        for base, dirs, files in os.walk(os.path.join(root, *rel_dir.split("/"))):
            dirs.sort()
            for f in sorted(files):
                if f.endswith(exts) and f != "ps2_addr_list.h":
                    full = os.path.join(base, f)
                    yield os.path.relpath(full, root).replace(os.sep, "/"), full


def scan(root):
    """Return (hits, entries): hits are (file, line, value, state) in walk
    order; entries are the parsed list."""
    with open(os.path.join(root, *LIST_REL.split("/")), encoding="utf-8", newline="") as fp:
        entries = parse_list(fp.read())
    listed = {e.us for e in entries}
    # A RET entry is also written as its address + 1 (an exclusive range end).
    listed |= {e.us + 1 for e in entries if e.kind == "RET"}
    allowed = {(f, v) for f, v, _ in ALLOW}
    hits = []
    for rel, full in _sources(root):
        with open(full, encoding="utf-8", errors="replace", newline="") as fp:
            for lineno, line in enumerate(fp.read().split("\n"), 1):
                for m in _LITERAL.finditer(line):
                    value = int(m.group(1), 16)
                    if not LO <= value < HI:
                        continue
                    if (rel, value) in allowed:
                        state = ALLOWED
                    elif value in listed:
                        state = PENDING
                    else:
                        state = UNKNOWN
                    hits.append((rel, lineno, value, state))
    return hits, entries


def run(root, strict=False, log=print):
    """Print the report and return the exit code."""
    hits, _ = scan(root)
    n = {ALLOWED: 0, PENDING: 0, UNKNOWN: 0}
    for rel, lineno, value, state in hits:
        n[state] += 1
        if state != ALLOWED:
            log("%s:%d 0x%08X %s" % (rel, lineno, value, state))
    log("regionaddr lint: %d allowed, %d pending migration, %d unknown"
        % (n[ALLOWED], n[PENDING], n[UNKNOWN]))
    return 1 if n[UNKNOWN] or (strict and n[PENDING]) else 0
