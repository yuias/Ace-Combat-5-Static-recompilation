"""Locate clang and SDL3 for the C-based tests (clang targeting x86_64-pc-windows-msvc)."""

import glob
import os
import shutil

import paths

DEPS = os.path.join(paths.ROOT, "deps")


def _latest(pattern):
    hits = sorted(glob.glob(os.path.join(DEPS, pattern)))
    return hits[-1] if hits else None


def tool(name):
    """<name> from PATH."""
    found = shutil.which(name)
    if not found:
        raise SystemExit("%s not found on PATH; install LLVM 19 or newer" % name)
    return found


def cc():
    return os.environ.get("CC") or tool("clang")


def target_flags():
    """Flags every direct compile needs; they match the CMake build."""
    return ["--target=x86_64-pc-windows-msvc", "-fuse-ld=lld",
            "-fms-runtime-lib=static", "-D_CRT_SECURE_NO_WARNINGS",
            "-D_CRT_NONSTDC_NO_DEPRECATE"]


def _sdl3_root():
    return _latest(os.path.join("sdl3-vc", "SDL3-*"))


def sdl3_flags():
    """Compile and link flags for SDL3, from the VC devel package in deps/."""
    sdl = _sdl3_root()
    if not sdl:
        return ["-lSDL3"]
    return ["-I" + os.path.join(sdl, "include"),
            "-L" + os.path.join(sdl, "lib", "x64"), "-lSDL3"]


def sdl3_dll():
    """SDL3.dll from deps/, for copying next to a test executable."""
    sdl = _sdl3_root()
    dll = sdl and os.path.join(sdl, "lib", "x64", "SDL3.dll")
    return dll if dll and os.path.exists(dll) else None
