import importlib
import os
import shutil
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import regions

US_EXE = os.path.join(ROOT, "tmp", "us", "SLUS_208.51")
JP_EXE = os.path.join(ROOT, "tmp", "jp", "SLPS_254.18")

ENV_KEYS = ("AC5_REGION", "AC5_DISC")


def raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return True
    return False


def test_system_cnf():
    f = regions.exe_from_system_cnf
    assert f("BOOT2 = cdrom0:\\SLPS_254.18;1\r\nVER = 1.00\r\n") == "SLPS_254.18"
    assert f("BOOT2 = cdrom0:\\SLUS_208.51;1\nVER = 1.00\nVMODE = NTSC\n") == "SLUS_208.51"
    assert f("boot2=cdrom0:\\slps_254.18;1") == "slps_254.18"
    assert f("BOOT2=cdrom0:SLPS_254.18") == "SLPS_254.18"
    assert f("VER = 1.00\r\nVMODE = NTSC\r\n") is None
    assert f("") is None


def test_lookup():
    assert regions.by_exe_name("SLPS_254.18") is regions.JP
    assert regions.by_exe_name("slps_254.18;1") is regions.JP
    assert regions.by_exe_name("SLUS_208.51") is regions.US
    assert regions.by_exe_name("SLES_000.00") is None
    assert regions.by_key("us") is regions.US
    assert regions.by_key("jp") is regions.JP
    assert raises(ValueError, regions.by_key, "eu")
    assert regions.by_sha256(regions.JP.exe_sha256.upper()) is regions.JP
    assert regions.by_sha256("00") is None
    assert len({r.runtime_id for r in regions.REGIONS}) == len(regions.REGIONS)


def test_config_file():
    cf = regions.config_file
    r = "root"
    assert cf(regions.US, "ida_db.json", r) == os.path.join(r, "config", "ida_db.json")
    assert cf(regions.JP, "ida_db.json", r) == os.path.join(
        r, "config", "slps-25418", "ida_db.json")
    assert cf(regions.JP, "ee_syscalls.json", r) == os.path.join(
        r, "config", "ee_syscalls.json")
    assert cf(regions.JP, "pac_names.txt", r) == os.path.join(r, "config", "pac_names.txt")


def _load_paths(**env):
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env)
    import paths
    return importlib.reload(paths)


def test_paths_selection():
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    tmp = tempfile.mkdtemp()
    try:
        p = _load_paths(AC5_REGION="jp")
        assert p.REGION is regions.JP
        assert p.GAME.endswith("SLPS_254.18")
        assert p.region_config("ida_db.json").endswith(
            os.path.join("config", "slps-25418", "ida_db.json"))

        p = _load_paths(AC5_REGION="us")
        assert p.REGION is regions.US and p.GAME.endswith("SLUS_208.51")
        assert p.region_config("ida_db.json") == p.config("ida_db.json")

        p = _load_paths(AC5_REGION="xx")
        assert False, "bad AC5_REGION accepted"
    except SystemExit:
        pass
    try:
        for exe, want in (("SLPS_254.18", regions.JP), ("SLUS_208.51", regions.US)):
            disc = os.path.join(tmp, want.key)
            os.makedirs(disc)
            with open(os.path.join(disc, "SYSTEM.CNF"), "w", newline="\r\n") as f:
                f.write("BOOT2 = cdrom0:\\%s;1\nVER = 1.00\n" % exe)
            p = _load_paths(AC5_DISC=disc)
            assert p.REGION is want, (p.REGION.key, want.key)
            assert p.DISC == disc
            assert p.GAME == os.path.join(disc, exe)

        # An explicit region wins over the disc contents.
        p = _load_paths(AC5_DISC=os.path.join(tmp, "jp"), AC5_REGION="us")
        assert p.REGION is regions.US

        # A disc folder without SYSTEM.CNF falls back to US.
        bare = os.path.join(tmp, "bare")
        os.makedirs(bare)
        assert _load_paths(AC5_DISC=bare).REGION is regions.US
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        import paths
        importlib.reload(paths)
        shutil.rmtree(tmp)


def test_real_detect():
    assert regions.detect_elf(US_EXE) is regions.US
    assert regions.detect_elf(JP_EXE) is regions.JP
    for r, path in ((regions.US, US_EXE), (regions.JP, JP_EXE)):
        assert os.path.getsize(path) == r.exe_size


test_system_cnf()
test_lookup()
test_config_file()
test_paths_selection()

if os.path.isfile(US_EXE) and os.path.isfile(JP_EXE):
    test_real_detect()
    print("PASS: regions (real binaries)")
else:
    print("SKIP: real binaries not present")
    print("PASS: regions")
