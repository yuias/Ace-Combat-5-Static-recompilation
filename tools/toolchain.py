"""Locate the llvm-mingw toolchain and SDL3 that tools/build-clang.ps1 puts in deps/."""

import glob
import os
import shutil

import paths

DEPS = os.path.join(paths.ROOT, "deps")


def _latest(pattern):
    hits = sorted(glob.glob(os.path.join(DEPS, pattern)))
    return hits[-1] if hits else None


def tool(name):
    """deps/llvm-mingw*/bin/<name>, else <name> from PATH."""
    llvm = _latest("llvm-mingw-*-ucrt-x86_64")
    if llvm:
        exe = os.path.join(llvm, "bin", name + ".exe")
        if os.path.exists(exe):
            return exe
    found = shutil.which(name)
    if not found:
        raise SystemExit("%s not found; run tools/build-clang.ps1 once to fetch "
                         "llvm-mingw into deps/" % name)
    return found


def cc():
    return os.environ.get("CC") or tool("clang")


def sdl3_flags():
    """Compile and link flags for SDL3, from the mingw devel package in deps/."""
    sdl = _latest(os.path.join("SDL3-*", "x86_64-w64-mingw32"))
    if not sdl:
        return ["-lSDL3"]
    return ["-I" + os.path.join(sdl, "include"),
            "-L" + os.path.join(sdl, "lib"), "-lSDL3"]


def sdl3_dll():
    """SDL3.dll from deps/, for copying next to a test executable."""
    sdl = _latest(os.path.join("SDL3-*", "x86_64-w64-mingw32"))
    dll = sdl and os.path.join(sdl, "bin", "SDL3.dll")
    return dll if dll and os.path.exists(dll) else None
