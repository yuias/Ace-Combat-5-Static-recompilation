import hashlib
import os
import struct
from dataclasses import dataclass
from typing import List, Tuple

from ps2recomp.elf import ElfFile, PT_LOAD, SHT_NOBITS


@dataclass
class SecPair:
    name: str
    us_start: int
    us_end: int
    jp_start: int
    jp_end: int
    nobits: bool
    exec: bool


def file_info(elf: ElfFile) -> dict:
    return {
        "file": os.path.basename(elf.path),
        "size": len(elf.data),
        "sha256": hashlib.sha256(elf.data).hexdigest(),
        "entry": elf.entry,
    }


def _alloc_sections(elf: ElfFile) -> dict:
    out = {}
    for s in elf.sections:
        if s.is_alloc and s.size > 0 and s.name not in out:
            out[s.name] = s
    return out


def pair_sections(us: ElfFile, jp: ElfFile) -> Tuple[List[SecPair], List[str]]:
    """SHF_ALLOC sections with size > 0, paired by name. The second value
    lists names present in only one ELF (reported, not fatal)."""
    a, b = _alloc_sections(us), _alloc_sections(jp)
    pairs = []
    for name, s in a.items():
        t = b.get(name)
        if t is None:
            continue
        pairs.append(SecPair(name, s.addr, s.addr + s.size, t.addr, t.addr + t.size,
                             s.type == SHT_NOBITS, s.is_exec))
    pairs.sort(key=lambda p: p.us_start)
    only = sorted(set(a) ^ set(b))
    return pairs, only


def image_bounds(us: ElfFile, jp: ElfFile) -> Tuple[int, int]:
    """Union over both ELFs of the SHF_ALLOC section span [min start, max end)."""
    secs = list(_alloc_sections(us).values()) + list(_alloc_sections(jp).values())
    if not secs:
        raise ValueError("no allocated sections")
    return min(s.addr for s in secs), max(s.addr + s.size for s in secs)


def words(elf: ElfFile, start: int, end: int) -> List[int]:
    """Little-endian u32 list read from PT_LOAD file data. Raises ValueError
    if [start, end) is not fully backed by PT_LOAD filesz."""
    if end < start or (end - start) & 3:
        raise ValueError("bad word range %08X-%08X" % (start, end))
    for seg in elf.segments:
        if seg.type == PT_LOAD and seg.vaddr <= start and end <= seg.vaddr + seg.filesz:
            n = (end - start) >> 2
            return list(struct.unpack_from("<%dI" % n, seg.data, start - seg.vaddr))
    raise ValueError("range %08X-%08X is not backed by file data" % (start, end))
