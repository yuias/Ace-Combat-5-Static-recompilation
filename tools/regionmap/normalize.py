import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ps2recomp import r5900
from ps2recomp.elf import ElfFile

from .common import words as read_words

REF_CALL, REF_ABS, REF_GP = 1, 2, 3

# Opcode sets are derived from the decoder tables once; the per-word loop below
# works on raw bit fields because a decoded Insn per word is too slow for ~0.67M
# words per stream.
_BY_NAME = {name: op for op, name in r5900.OPCODE.items()}
_LO16_NAMES = r5900.LOADS | r5900.STORES | {"ADDIU", "ADDI", "DADDIU", "ORI"}
LO16_OPS = frozenset(_BY_NAME[n] for n in _LO16_NAMES)
# lo16 ops that write a GPR: ADDIU-style ops and loads, except the FPU/VU2 loads.
RT_WRITERS = frozenset(_BY_NAME[n] for n in
                       (r5900.LOADS - {"LWC1", "LQC2"}) | {"ADDIU", "ADDI", "DADDIU", "ORI"})
CLEAR_RT_OPS = frozenset(_BY_NAME[n] for n in ("ANDI", "XORI", "SLTI", "SLTIU", "DADDI"))

OP_SPECIAL, OP_J, OP_JAL, OP_ORI, OP_LUI, OP_MMI = 0x00, 0x02, 0x03, _BY_NAME["ORI"], 0x0F, 0x1C
SPECIAL_ADDS = frozenset((0x20, 0x21, 0x2D))  # ADD, ADDU, DADDU
MOVE_FUNCTS = frozenset((0x21, 0x25, 0x2D))  # ADDU, OR, DADDU with $zero as rt
GP_REG = 28
# Registers a call may clobber: at, v0-v1, a0-a3, t0-t7, t8-t9, ra.
CALLER_SAVED = (*range(1, 16), 24, 25, 31)
SCAN_WORDS = 256  # gp setup is ~110 words past the entry point


@dataclass
class Stream:
    base: int                         # address of raw[0]
    raw: List[int]
    norm: List[int]
    refs: Dict[int, Tuple[int, int]]  # word index -> (kind, target address)
    stats: Dict[str, int]             # masked counts: call, lui, abs, gp

    def index(self, addr: int) -> int:
        return (addr - self.base) >> 2

    def addr(self, idx: int) -> int:
        return self.base + (idx << 2)


def hi_range_for(bounds: Tuple[int, int]) -> Tuple[int, int]:
    """lui immediates that can start an in-image address, from the union image
    bounds of both ELFs (+0x7FFF covers the sign-extended low half)."""
    lo, hi = bounds
    return lo >> 16, (hi + 0x7FFF) >> 16


def _gpr_dest(w: int) -> Optional[int]:
    """GPR written by an instruction word, for the instruction classes the
    normalizer tracks (None when it writes no GPR or is not recognized)."""
    op = w >> 26
    if op == OP_SPECIAL or op == OP_MMI:
        return (w >> 11) & 31
    if op == OP_JAL:
        return 31
    if op == OP_LUI or op in RT_WRITERS or op in CLEAR_RT_OPS:
        return (w >> 16) & 31
    if 0x10 <= op <= 0x12 and ((w >> 21) & 31) <= 2:
        return (w >> 16) & 31
    return None


def _reginfo_gp(elf: ElfFile) -> Optional[int]:
    # ri_gp_value is the sixth word of the .reginfo record.
    sec = elf.section(".reginfo")
    if sec is None or len(sec.data) < 24:
        return None
    return struct.unpack_from("<I", sec.data, 20)[0] or None


def scan_gp(elf: ElfFile) -> Optional[int]:
    """gp as built by the startup code: `lui`/`addiu` (or `ori`) either directly
    in gp or in a scratch register that is then moved into gp (the entry stub in
    these executables does the latter, about a hundred words in). A register's
    constant is dropped when any other instruction writes it; the scan stops at
    the first call or return, after which register contents are unknown.
    Plain `j` is not a stop: the stub's clear loops use it as a back edge."""
    consts: Dict[int, int] = {}
    addiu = _BY_NAME["ADDIU"]
    for k in range(SCAN_WORDS):
        w = elf.word(elf.entry + 4 * k)
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op == OP_JAL or (op == OP_SPECIAL and (w & 63) in (0x08, 0x09)):
            break
        if op == OP_LUI:
            consts[rt] = (imm << 16) & 0xFFFFFFFF
        elif op in (addiu, OP_ORI) and rs == rt and rs in consts:
            off = imm if op == OP_ORI else (imm - 0x10000 if imm & 0x8000 else imm)
            consts[rt] = (consts[rs] + off) & 0xFFFFFFFF if op == addiu else consts[rs] | imm
            if rt == GP_REG:
                return consts[rt]
        elif op == OP_SPECIAL and (w & 63) in MOVE_FUNCTS and ((w >> 11) & 31) == GP_REG \
                and rt == 0 and rs in consts:
            return consts[rs]
        else:
            d = _gpr_dest(w)
            if d is not None:
                consts.pop(d, None)
    return None


def find_gp(elf: ElfFile) -> Optional[int]:
    """gp from `.reginfo`, else the startup scan, else the `_gp` symbol."""
    gp = _reginfo_gp(elf) or scan_gp(elf)
    if gp is not None:
        return gp
    for s in elf.symbols:
        if s.name == "_gp":
            return s.value
    return None


def normalize_stream(words: List[int], base: int, hi_range: Tuple[int, int],
                     gp: Optional[int]) -> Stream:
    """Mask address-dependent immediates and record the references they carry.
    Linear over the whole stream, so both ELFs must get the same hi_range."""
    hi_lo, hi_hi = hi_range
    hi: List[Optional[int]] = [None] * 32
    norm = list(words)
    refs: Dict[int, Tuple[int, int]] = {}
    n_call = n_lui = n_abs = n_gp = 0
    lo16_ops, rt_writers, clear_rt = LO16_OPS, RT_WRITERS, CLEAR_RT_OPS

    # Register state does not survive a jump: without the reset a lui from the
    # previous function (or an argument register after a call) would make later
    # unrelated instructions look like address pairs, so identical functions
    # could normalize differently depending on their predecessor. The reset
    # applies after the delay slot has been processed.
    pending = 0  # 0: none, 1: clear all, 2: clear caller-saved registers
    caller_saved = CALLER_SAVED

    for i, w in enumerate(words):
        reset, pending = pending, 0
        op = w >> 26
        if op in lo16_ops:
            rs = (w >> 21) & 31
            imm = w & 0xFFFF
            if rs == GP_REG and gp is not None:
                norm[i] = w & 0xFFFF0000
                refs[i] = (REF_GP, (gp + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xFFFFFFFF)
                n_gp += 1
            elif hi[rs] is not None:
                # Targets can fall outside the image (in-range bit masks such as
                # lui 0x10; addiu -1 look the same); consumers must filter them.
                off = imm if op == OP_ORI else (imm - 0x10000 if imm & 0x8000 else imm)
                norm[i] = w & 0xFFFF0000
                refs[i] = (REF_ABS, ((hi[rs] << 16) + off) & 0xFFFFFFFF)
                n_abs += 1
            if op in rt_writers:
                hi[(w >> 16) & 31] = None
        elif op == OP_LUI:
            rt = (w >> 16) & 31
            imm = w & 0xFFFF
            if hi_lo <= imm <= hi_hi:
                norm[i] = w & 0xFFFF0000
                n_lui += 1
                hi[rt] = imm if rt else None
            else:
                hi[rt] = None
        elif op == OP_SPECIAL:
            rd = (w >> 11) & 31
            if (w & 63) in SPECIAL_ADDS:
                a, b = hi[(w >> 21) & 31], hi[(w >> 16) & 31]
                # Indexed table access: lui at; addu at,at,v0; lw x,lo(at)
                if (a is None) != (b is None) and rd:
                    hi[rd] = a if a is not None else b
                else:
                    hi[rd] = None
            else:
                hi[rd] = None
                funct = w & 63
                if funct == 0x08:      # JR
                    pending = 1
                elif funct == 0x09:    # JALR
                    pending = 2
        elif op == OP_J or op == OP_JAL:
            norm[i] = w & 0xFC000000
            refs[i] = (REF_CALL, ((base + 4 * i + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2))
            n_call += 1
            if op == OP_JAL:
                hi[31] = None
                pending = 2
            else:
                pending = 1
        elif op == OP_MMI:
            hi[(w >> 11) & 31] = None
        elif op in clear_rt:
            hi[(w >> 16) & 31] = None
        elif 0x10 <= op <= 0x12 and ((w >> 21) & 31) <= 2:
            hi[(w >> 16) & 31] = None

        if reset == 1:
            hi = [None] * 32
        elif reset == 2:
            for r in caller_saved:
                hi[r] = None

    return Stream(base, list(words), norm, refs,
                  {"call": n_call, "lui": n_lui, "abs": n_abs, "gp": n_gp})


def text_stream(elf: ElfFile, hi_range: Tuple[int, int], gp: Optional[int]) -> Stream:
    ranges = elf.text_ranges()
    if len(ranges) != 1:
        raise ValueError("%s: expected one text range, found %d"
                         % (elf.path, len(ranges)))
    start, end = ranges[0]
    return normalize_stream(read_words(elf, start, end), start, hi_range, gp)
