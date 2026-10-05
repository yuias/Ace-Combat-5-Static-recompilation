"""Supported game regions, identified by the exact executable (SHA-256).

Pure data plus small helpers.  Must not import paths.py (paths imports this).
"""
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Region:
    key: str            # "us" | "jp"
    game_id: str        # "SLUS-20851"
    exe_name: str       # name on the disc, as in SYSTEM.CNF BOOT2
    exe_size: int
    exe_sha256: str     # lowercase hex
    disc_name: str      # default extracted-disc folder name
    config_dir: str     # repo-relative, "/" separators
    generated_dir: str  # repo-relative, "/" separators
    runtime_id: int     # matches PS2_REGION_* in runtime/include/ps2_region.h


US = Region("us", "SLUS-20851", "SLUS_208.51", 3634092,
            "c3594227605307806592416dbd723bf5771952e279ee281a40da305426667385",
            "Ace Combat 5 - The Unsung War (USA) (En,Ja)",
            "config", "generated", 0)
JP = Region("jp", "SLPS-25418", "SLPS_254.18", 3634988,
            "b510ee45343325bdacf14b81e3a1b34de103c1f0379beaa014795a4e401025ad",
            "Ace Combat 5 - The Unsung War (Japan)",
            "config/slps-25418", "generated/slps-25418", 1)
REGIONS = (US, JP)

# No executable addresses in these; every region reads them from config/.
# pac_names.txt is per region: the DATA.PAC member layouts differ.
NEUTRAL_CONFIGS = frozenset({"ee_syscalls.json"})

_BOOT2 = re.compile(r"^\s*BOOT2\s*=\s*cdrom\d*:[\\/]*([^;\s]+)",
                    re.IGNORECASE | re.MULTILINE)


def by_key(key: str) -> Region:
    for r in REGIONS:
        if r.key == key:
            return r
    raise ValueError("unknown region %r (valid: %s)"
                     % (key, ", ".join(r.key for r in REGIONS)))


def by_sha256(digest: str) -> Optional[Region]:
    digest = digest.strip().lower()
    for r in REGIONS:
        if r.exe_sha256 == digest:
            return r
    return None


def by_exe_name(name: str) -> Optional[Region]:
    name = name.strip()
    if name.endswith(";1"):
        name = name[:-2]
    name = name.lower()
    for r in REGIONS:
        if r.exe_name.lower() == name:
            return r
    return None


def detect_elf(path: str) -> Optional[Region]:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return by_sha256(h.hexdigest())


def exe_from_system_cnf(text: str) -> Optional[str]:
    m = _BOOT2.search(text)
    return m.group(1) if m else None


def config_file(region: Region, name: str, root: str) -> str:
    if region is US or name in NEUTRAL_CONFIGS:
        return os.path.join(root, "config", name)
    return os.path.join(root, *region.config_dir.split("/"), name)
