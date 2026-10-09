"""Locate clang and the GameInput package for the C-based tests (clang targeting x86_64-pc-windows-msvc)."""

import glob
import os
import shutil

import paths

DEPS = os.path.join(paths.ROOT, "deps")


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


def gameinput_root():
    """Newest verified deps/gameinput/<version>, or None."""
    hits = sorted(glob.glob(os.path.join(DEPS, "gameinput", "*", ".verified-sha256")))
    return os.path.dirname(hits[-1]) if hits else None


def gameinput_flags():
    """Compile and link flags for GameInput: the package's v3 header and its static loader."""
    root = gameinput_root()
    if not root:
        raise SystemExit("the GameInput package is missing; run tools/build-clang.ps1 first")
    # The loader library is passed by path: the Windows SDK has an import library of the same name.
    return ["-isystem", os.path.join(root, "native", "include"),
            os.path.join(root, "native", "lib", "x64", "GameInput.lib")]
