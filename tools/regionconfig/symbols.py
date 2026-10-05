"""Handlers for the address-keyed symbol tables: hooks, overrides, symbol maps."""
import json
import os
import re
from typing import Callable, Dict, Optional

import regions

from .core import Translator, dump_json, fmt_like

_GAME_LINE = re.compile(r"^([0-9A-Fa-f]{8})(?=\s)")


def _read(src_dir: str, name: str) -> bytes:
    path = os.path.join(src_dir, name)
    if not os.path.isfile(path):
        raise SystemExit("regionconfig: missing source file %s" % path.replace("\\", "/"))
    with open(path, "rb") as fp:
        return fp.read()


def _is_addr(key) -> bool:
    return isinstance(key, str) and key[:2].lower() == "0x"


def _note_if_body_changed(tr: Translator, name: str, where: str, us: int, jp: int) -> None:
    fm = tr.rmap.function_at(us)
    if fm is not None and fm.status == "body-changed":
        tr.note(name, where, us, jp, "target is in body-changed %s" % fm.name)


def _translate_table(tr: Translator, src_dir: str, name: str, lookup: Callable,
                     strict: bool, note_body: bool,
                     must_keep: Optional[Callable] = None) -> Dict[str, bytes]:
    """Translate the 0x-keys of a JSON object. Other keys (names, comments)
    are copied. strict: an unmapped or colliding key fails instead of being
    dropped. must_keep(value) makes a single entry strict."""
    raw = _read(src_dir, name)
    stem = name[:-len(".json")]
    out = {}
    for key, val in json.loads(raw).items():
        if not _is_addr(key):
            out[key] = val
            tr.copy(name)
            continue
        us = int(key, 16)
        where = "%s:%s" % (stem, key)
        jp = lookup(us)
        if jp is None:
            if strict or (must_keep is not None and must_keep(val)):
                tr.fail(name, where, us, tr.why(us))
            else:
                tr.drop(name, where, us)
            continue
        new = fmt_like(key, jp)
        if new in out:
            reason = "maps onto 0x%08X, already taken" % jp
            if strict or (must_keep is not None and must_keep(val)):
                tr.fail(name, where, us, reason)
            else:
                tr.drop(name, where, us, reason)
            continue
        out[new] = val
        tr.kept(name)
        if note_body:
            _note_if_body_changed(tr, name, where, us, jp)
    return {name: dump_json(out, 1, raw)}


def handle_hooks(tr: Translator, src_dir: str) -> Dict[str, bytes]:
    return _translate_table(tr, src_dir, "hooks.json", tr.func_entry, True, True)


def handle_overrides(tr: Translator, src_dir: str) -> Dict[str, bytes]:
    return _translate_table(tr, src_dir, "overrides.json", tr.func_entry, True, True)


def handle_manual_symbols(tr: Translator, src_dir: str) -> Dict[str, bytes]:
    # These names back the name-based overrides, so none may be lost.
    return _translate_table(tr, src_dir, "manual_symbols.json", tr.addr, True, False)


def handle_sdk_symbols(tr: Translator, src_dir: str) -> Dict[str, bytes]:
    # A name that an override or hook looks up must survive translation.
    wanted = set(json.loads(_read(src_dir, "overrides.json")))
    wanted |= set(json.loads(_read(src_dir, "hooks.json")))

    def must_keep(val) -> bool:
        return isinstance(val, dict) and val.get("name") in wanted

    return _translate_table(tr, src_dir, "sdk_symbols.json", tr.addr, False, True,
                            must_keep)


def handle_game_symbols(tr: Translator, src_dir: str) -> Dict[str, bytes]:
    name = "game_symbols.txt"
    text = _read(src_dir, name).decode("utf-8")
    lines = text.split("\n")
    out = []
    seen = set()
    for i, line in enumerate(lines):
        if i == 0:
            out.append("# Names for %s's functions, translated from config/%s by "
                       "tools/regionconfig." % (regions.JP.exe_name, name))
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            out.append(line)
            continue
        m = _GAME_LINE.match(line)
        if m is None:
            tr.fail(name, "game_symbols:line %d" % (i + 1), None, "unparseable line")
            continue
        us = int(m.group(1), 16)
        where = "game_symbols:0x%08X" % us
        jp = tr.addr(us)
        if jp is None:
            tr.drop(name, where, us)
        elif jp in seen:
            tr.drop(name, where, us, "maps onto 0x%08X, already taken" % jp)
        else:
            seen.add(jp)
            out.append(fmt_like(m.group(1), jp) + line[8:])
            tr.kept(name)
    return {name: "\n".join(out).encode("utf-8")}
