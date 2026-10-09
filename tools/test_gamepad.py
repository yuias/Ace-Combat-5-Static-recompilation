from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolchain

root = Path(__file__).resolve().parents[1]
out = root / 'out' / 'gamepad_test'
out.mkdir(parents=True, exist_ok=True)

os_obj = out / 'ps2_os.o'
subprocess.run([toolchain.cc(), *toolchain.target_flags(),
                '-std=gnu2x', '-O1', '-c',
                '-I' + str(root / 'runtime/include'),
                str(root / 'runtime/src/ps2_os.c'),
                '-o', str(os_obj)], check=True)

cxx_flags = ['-std=c++17', '-O1', '-Wall', '-Wextra', '-Wno-unused-parameter',
             '-fno-exceptions', '-fno-rtti', '-D_HAS_EXCEPTIONS=0',
             '-I' + str(root / 'runtime/include')]

# The hub against a fake backend: no GameInput needed.
hub_exe = out / 'gamepad_test.exe'
subprocess.run([toolchain.cc(), *toolchain.target_flags(), *cxx_flags,
                str(root / 'tools/gamepad_test.cpp'),
                str(root / 'runtime/src/ps2_gamepad.cpp'),
                str(os_obj), '-o', str(hub_exe)], check=True)
subprocess.run([str(hub_exe)], check=True, timeout=60)

# The hub with the real GameInput backend. Needs the installed runtime; the
# test reports "skipped" itself when the runtime is missing.
gi_exe = out / 'gamepad_gi_test.exe'
subprocess.run([toolchain.cc(), *toolchain.target_flags(), *cxx_flags,
                str(root / 'tools/gamepad_gi_test.cpp'),
                str(root / 'runtime/src/ps2_gamepad.cpp'),
                str(root / 'runtime/src/ps2_gamepad_gi.cpp'),
                str(os_obj), *toolchain.gameinput_flags(),
                '-o', str(gi_exe)], check=True)
subprocess.run([str(gi_exe)], check=True, timeout=60)
