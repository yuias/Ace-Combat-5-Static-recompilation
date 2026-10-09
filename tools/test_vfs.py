from pathlib import Path
import os
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths
import toolchain

root = Path(paths.ROOT)
out = root / 'out' / 'vfs_test'
work = out / 'work'
work.mkdir(parents=True, exist_ok=True)
exe = out / 'vfs_test.exe'
# AC5_DISC may name the extracted disc folder or a disc image; with a folder,
# a same-named .iso next to it is checked too.
disc = Path(paths.DISC)
if disc.suffix.lower() == '.iso':
    tree, iso = None, disc
else:
    tree, iso = disc, Path(str(disc) + '.iso')
if tree is not None and not tree.is_dir():
    tree = None
if not iso.is_file():
    iso = None
if tree is None and iso is None:
    raise SystemExit('no disc at %s; set AC5_DISC to the extracted disc folder '
                     'or to the .iso' % disc)
images = [p for p in (tree, iso) if p is not None]
names = Path(paths.region_config('pac_names.txt'))

subprocess.run([toolchain.cc(), *toolchain.target_flags(),
                '-std=gnu2x', '-O2', '-fno-strict-aliasing', '-fwrapv',
                '-Wall', '-Wextra', '-Wno-unused-parameter',
                '-I' + str(root / 'runtime/include'),
                str(root / 'tools/vfs_test.c'),
                str(root / 'runtime/src/ps2_vfs.c'),
                str(root / 'runtime/src/ps2_os.c'),
                '-o', str(exe)], check=True)

env = dict(os.environ, PS2_MOD_NAMES=str(names))


def run(*args):
    r = subprocess.run([str(exe), *map(str, args)], env=env,
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode:
        raise SystemExit('vfs_test %s failed (%d)' % (args[0], r.returncode))
    return r.stdout


for image in images:
    run('identity', image)

def pattern(seed, n):
    return bytes((seed * 31 + i * 7) & 0xFF for i in range(n))


files = {
    'noise_a.bin': pattern(1, 12400),
    'noise_hi.bin': pattern(2, 9001),
    'noise_lo.bin': pattern(3, 100),
    'wad.bin': pattern(4, 1001),
    'light_c.bin': pattern(5, 4096),
    'light_s.bin': pattern(6, 777),
    'index3.bin': pattern(7, 33),
    'bgm.bin': pattern(8, 4096 + 5),
    'new.bin': pattern(9, 64),
    'dummy16.bin': pattern(10, 16),
}
for name, data in files.items():
    (work / name).write_bytes(data)
run('cat', images[0], 'BIN/DATA.TBL', work / 'tbl.bin')

light = [line.split()[0] for line in names.read_text().splitlines()
         if line.endswith(' mistitle/backlight.gim')]
for image in images:
    run('mods', image, work, light[3])

if tree is not None and iso is not None:
    probe = ['BIN/DATA.TBL', 'BIN/DATA.PAC', 'BIN/US/BGM.PAC', 'SYSTEM.CNF',
             'SLUS_208.51']
    a = run('digest', tree, *probe)
    b = run('digest', iso, *probe)
    if a != b:
        raise SystemExit('the .iso and the extracted tree read differently')
    print('digest: .iso and tree agree on %d files and %d members'
          % (len(probe), a.count('member ')))
else:
    print('digest: only %s, image comparison skipped' % images[0].name)
print('vfs: all checks passed')
