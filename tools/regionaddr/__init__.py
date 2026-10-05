"""Per-region runtime addresses: the list of guest addresses the runtime uses
(runtime/include/ps2_addr_list.h) and the tools that check it."""
import re
from typing import List, NamedTuple

KINDS = ("FUNC", "RET", "CODE", "END", "DATA")

# US addresses must be written as 8 digits so tools/regionmap keeps seeing them
# as anchors in this header.
_ENTRY = re.compile(r"^\s*PS2_ADDR\(\s*(\w+)\s*,\s*(0x00[0-9A-Fa-f]{6})u\s*,"
                    r"\s*(FUNC|RET|CODE|END|DATA)\s*,\s*([0-8])\s*\)\s*$")


class ListError(ValueError):
    """Malformed list file; the message starts with the line number."""


class Entry(NamedTuple):
    name: str
    us: int
    kind: str
    nwords: int
    line: int


def parse_list(text: str) -> List[Entry]:
    """Parse the X-macro list. Only comments, blank lines and PS2_ADDR(...)
    lines are allowed; names are unique and one address is not listed twice
    with the same kind."""
    entries: List[Entry] = []
    by_name = {}
    by_value = {}
    comment_from = 0
    for lineno, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if comment_from:
            if "*/" in line:
                if line.split("*/", 1)[1].strip():
                    raise ListError("line %d: text after the end of a comment" % lineno)
                comment_from = 0
            continue
        if not line:
            continue
        if line.startswith("/*"):
            if "*/" in line:
                if line.split("*/", 1)[1].strip():
                    raise ListError("line %d: text after the end of a comment" % lineno)
            else:
                comment_from = lineno
            continue
        m = _ENTRY.match(line)
        if not m:
            raise ListError("line %d: not a PS2_ADDR(name, 0x00XXXXXXu, KIND, words) "
                            "entry: %s" % (lineno, line))
        name, us, kind, nwords = m.group(1), int(m.group(2), 16), m.group(3), int(m.group(4))
        if name in by_name:
            raise ListError("line %d: duplicate name %s (first at line %d)"
                            % (lineno, name, by_name[name]))
        if (us, kind) in by_value:
            raise ListError("line %d: %s repeats %08X with kind %s (already %s)"
                            % (lineno, name, us, kind, by_value[(us, kind)]))
        by_name[name] = lineno
        by_value[(us, kind)] = name
        entries.append(Entry(name, us, kind, nwords, lineno))
    if comment_from:
        raise ListError("line %d: comment is not closed" % comment_from)
    return entries
