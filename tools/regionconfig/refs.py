"""Handlers for the reference configs: render_emitters, rpc_sids, ac5_syscalls_used.

Nothing in the build reads these files; they document the executable they
were extracted from. An address that does not translate is dropped and
listed in the manifest rather than failing the run."""
import json
import os
from typing import Dict, Optional

import regions

from .core import Translator, dump_json


def _read(src_dir: str, name: str) -> bytes:
    path = os.path.join(src_dir, name)
    if not os.path.isfile(path):
        raise SystemExit("regionconfig: missing source file %s" % path.replace("\\", "/"))
    with open(path, "rb") as fp:
        return fp.read()


def _hex8(v: int) -> str:
    return "%08X" % v


def _hexn(v: int) -> str:
    return "0x%X" % v


def _kept(tr: Translator, name: str) -> int:
    return tr.counts.get(name, {}).get("kept", 0)


def _bare(key: str) -> bool:
    return not key.lower().startswith("0x")


def _addr_key(tr: Translator, name: str, where: str, key: str, taken, lookup) -> Optional[str]:
    """Translate a bare-hex key. A miss or a collision is dropped and logged."""
    us = int(key, 16)
    jp = lookup(us)
    if jp is None:
        tr.drop(name, where, us)
        return None
    new = _hex8(jp)
    if new in taken:
        tr.drop(name, where, us, "maps onto 0x%08X, already taken" % jp)
        return None
    return new


def handle_render_emitters(tr: Translator, src_dir: str) -> Dict[str, bytes]:
    name = "render_emitters.json"
    raw = _read(src_dir, name)
    src = json.loads(raw)
    keys = list(src.get("emitters", ())) + list(src.get("virtual_methods", ()))
    if not all(_bare(k) for k in keys):
        tr.fail(name, "render_emitters", None, "unexpected 0x-prefixed key")
        return {name: raw}
    per = {"emitters": 0, "sites": 0, "ancestors": 0, "virtual methods": 0}
    emitters = {}
    for key, em in src.get("emitters", {}).items():
        us = int(key, 16)
        base = "emitters:0x%08X" % us
        new = _addr_key(tr, name, base, key, emitters, tr.func_entry)
        if new is None:
            per["emitters"] += 1
            continue
        tr.kept(name)
        out = dict(em)
        out["sites"] = []
        for site in em.get("sites", ()):
            s_us = int(site["site"], 16)
            jp = tr.addr(s_us)
            if jp is None:
                tr.drop(name, "%s/site:0x%08X" % (base, s_us), s_us)
                per["sites"] += 1
                continue
            tr.kept(name)
            out["sites"].append(dict(site, site=_hex8(jp)))
        out["ancestors"] = []
        for anc in em.get("ancestors", ()):
            a_us = int(anc["func"], 16)
            jp = tr.func_entry(a_us)
            if jp is None:
                tr.drop(name, "%s/ancestor:0x%08X" % (base, a_us), a_us)
                per["ancestors"] += 1
                continue
            tr.kept(name)
            out["ancestors"].append(dict(anc, func=_hex8(jp)))
        emitters[new] = out
    methods = {}
    for key, val in src.get("virtual_methods", {}).items():
        new = _addr_key(tr, name, "virtual_methods:0x%08X" % int(key, 16), key, methods,
                        tr.func_entry)
        if new is None:
            per["virtual methods"] += 1
            continue
        tr.kept(name)
        methods[new] = val
    # The extractor writes sorted keys; translation can reorder addresses.
    result = {}
    for k, v in src.items():
        if k == "binary":
            result[k] = regions.JP.exe_name
        elif k == "emitters":
            result[k] = dict(sorted(emitters.items()))
        elif k == "virtual_methods":
            result[k] = dict(sorted(methods.items()))
        else:
            result[k] = v
    print("render_emitters: kept %d, dropped %d (%s)"
          % (_kept(tr, name), sum(per.values()),
             ", ".join("%s %d" % kv for kv in per.items())))
    return {name: dump_json(result, 1, raw)}


def handle_rpc_sids(tr: Translator, src_dir: str) -> Dict[str, bytes]:
    name = "rpc_sids.json"
    raw = _read(src_dir, name)
    out = {}
    dropped = {}
    for key, rows in json.loads(raw).items():
        if not isinstance(rows, list):
            out[key] = rows
            continue
        out[key] = []
        dropped[key] = 0
        for row in rows:
            # sid and fno are an RPC server id and a function number, not
            # addresses, so they are copied.
            site, fn = int(row["site"], 16), int(row["fn"], 16)
            where = "%s:0x%08X" % (key, site)
            j_site, j_fn = tr.addr(site), tr.func_entry(fn)
            if j_site is None:
                tr.drop(name, where, site)
            elif j_fn is None:
                tr.drop(name, where, site, "fn 0x%08X: %s" % (fn, tr.why(fn)))
            else:
                tr.kept(name)
                out[key].append(dict(row, site=_hexn(j_site), fn=_hexn(j_fn)))
                continue
            dropped[key] += 1
    print("rpc_sids: kept %d, dropped %d (%s)"
          % (_kept(tr, name), sum(dropped.values()),
             ", ".join("%s %d" % kv for kv in dropped.items())))
    return {name: dump_json(out, 1, raw)}


def handle_syscalls_used(tr: Translator, src_dir: str) -> Dict[str, bytes]:
    name = "ac5_syscalls_used.json"
    raw = _read(src_dir, name)
    out = {}
    dropped = 0
    for num, entry in json.loads(raw).items():
        sites = []
        for s in entry.get("sites", ()):
            us = int(s, 16)
            where = "sites[%s]:0x%08X" % (num, us)
            jp = tr.addr(us)
            if jp is None:
                tr.drop(name, where, us)
            elif _hex8(jp) in sites:
                tr.drop(name, where, us, "maps onto 0x%08X, already taken" % jp)
            else:
                tr.kept(name)
                sites.append(_hex8(jp))
                continue
            dropped += 1
        # A selector stays listed when all its sites are gone: its names are
        # still the SDK's.
        out[num] = dict(entry, sites=sites)
    print("ac5_syscalls_used: kept %d, dropped %d" % (_kept(tr, name), dropped))
    return {name: dump_json(out, 1, raw)}
