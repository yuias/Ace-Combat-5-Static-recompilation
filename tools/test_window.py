from pathlib import Path
import os
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolchain

vulkan_sdk = os.environ.get('VULKAN_SDK')
if not vulkan_sdk:
    raise SystemExit('VULKAN_SDK is not set; install the Vulkan SDK')

root = Path(__file__).resolve().parents[1]
out = root / 'out' / 'window_test'
out.mkdir(parents=True, exist_ok=True)
exe = out / 'window_test.exe'

subprocess.run([toolchain.cc(), *toolchain.target_flags(),
                '-std=c++17', '-O1', '-Wall', '-Wextra', '-Wno-unused-parameter',
                '-fno-exceptions', '-fno-rtti', '-D_HAS_EXCEPTIONS=0',
                '-I' + str(root / 'runtime/include'),
                '-I' + str(Path(vulkan_sdk) / 'Include'),
                str(root / 'tools/window_test.cpp'),
                str(root / 'runtime/src/ps2_window.cpp'),
                '-L' + str(Path(vulkan_sdk) / 'Lib'), '-lvulkan-1',
                '-luser32', '-limm32', '-o', str(exe)], check=True)
subprocess.run([str(exe)], check=True, timeout=60)
