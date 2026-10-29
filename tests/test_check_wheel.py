"""发布门 check_wheel.py 的 hermetic 测试（不需要真实 wheel）。"""
import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "check_wheel", ROOT / "tools" / "check_wheel.py")
cw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cw)

METADATA = "Metadata-Version: 2.1\nName: qqexport\nRequires-Python: >=3.12\n"


def make_wheel(path: Path, *, extra: tuple[str, ...] = (),
               skip: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in cw.REQUIRED:
            if name == skip:
                continue
            zf.writestr(name, "")
        for name in extra:
            zf.writestr(name, "")
        zf.writestr("qqexport-0.1.0.dist-info/METADATA", METADATA)
    return path


def test_valid_wheel_passes(tmp_path):
    wheel = make_wheel(tmp_path / "ok.whl")
    assert cw.check_wheel(wheel) == []


def test_reports_missing_module(tmp_path):
    wheel = make_wheel(tmp_path / "missing.whl", skip="qqexport/doctor.py")
    assert any("缺少模块" in p for p in cw.check_wheel(wheel))


def test_rejects_tests_and_data_files(tmp_path):
    wheel = make_wheel(tmp_path / "bad.whl",
                       extra=("tests/test_x.py", "keys.json",
                              "work/nt_msg.decrypted.db"))
    problems = cw.check_wheel(wheel)
    assert any("tests/" in p for p in problems)
    assert any("keys.json" in p for p in problems)
    assert any(".db" in p for p in problems)


def test_requires_python_must_be_declared(tmp_path):
    wheel = tmp_path / "nopy.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        for name in cw.REQUIRED:
            zf.writestr(name, "")
        zf.writestr("qqexport-0.1.0.dist-info/METADATA", "Name: qqexport\n")
    assert any("Requires-Python" in p for p in cw.check_wheel(wheel))


def test_missing_wheel_fails(tmp_path):
    assert cw.main([str(tmp_path)]) == 1
