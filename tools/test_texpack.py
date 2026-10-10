from pathlib import Path
import os
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolchain

root = Path(__file__).resolve().parents[1]
out = root / 'out' / 'texpack_test'
out.mkdir(parents=True, exist_ok=True)
exe = out / 'texpack_test.exe'
pack = out / 'pack'

subprocess.run([toolchain.cc(), *toolchain.target_flags(),
                '-std=c++17', '-O1', '-Wall', '-Wextra', '-Wno-unused-parameter',
                '-fno-exceptions', '-fno-rtti', '-D_HAS_EXCEPTIONS=0', '-DPS2_BUILD_REGION=0',
                '-I' + str(root / 'runtime/include'),
                str(root / 'tools/texpack_test.cpp'),
                str(root / 'runtime/src/ps2_texpack.cpp'),
                '-lole32', '-lwindowscodecs', '-o', str(exe)], check=True)

# Paths are relative to the repository root so that the activation log line is
# the same on every machine.
rel_pack = 'out/texpack_test/pack'
base = {k: v for k, v in os.environ.items() if not k.startswith('PS2_TEX_PACK')}
try:
    for mode, env in (('off', {'PS2_TEX_PACK': '0'}),
                      ('on', {'PS2_TEX_PACK': '1', 'PS2_TEX_PACK_DIR': rel_pack})):
        shutil.rmtree(pack, ignore_errors=True)
        print('mode ' + mode, flush=True)
        subprocess.run([str(exe), mode], check=True, cwd=root, env=dict(base, **env), timeout=60)
finally:
    shutil.rmtree(pack, ignore_errors=True)
