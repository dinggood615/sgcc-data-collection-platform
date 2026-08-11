from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.sgcc import archive as archive_module
from app.sgcc.archive import UnsafeArchive, extract_archive_safely, extract_zip_safely


def test_safe_zip_extracts_regular_document(tmp_path: Path):
    archive = tmp_path / "notice.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        output.writestr("附件/项目清单.txt", "项目名称：信息系统开发服务")
    extracted = extract_zip_safely(archive, tmp_path / "output")
    assert [path.name for path in extracted] == ["项目清单.txt"]
    assert extracted[0].read_text(encoding="utf-8").startswith("项目名称")


def test_zip_path_traversal_is_rejected(tmp_path: Path):
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("../../escape.txt", "no")
    with pytest.raises(UnsafeArchive, match="路径穿越"):
        extract_zip_safely(archive, tmp_path / "output")


def test_executable_in_zip_is_rejected(tmp_path: Path):
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("run.exe", b"MZ")
    with pytest.raises(UnsafeArchive, match="禁止执行"):
        extract_zip_safely(archive, tmp_path / "output")


def test_7z_is_preflighted_and_extracted(monkeypatch, tmp_path: Path):
    source = tmp_path / "notice.7z"
    source.write_bytes(b"7z")
    monkeypatch.setattr(archive_module, "_seven_zip_executable", lambda: "/usr/bin/7zz")
    monkeypatch.setattr(
        archive_module,
        "_seven_zip_entries",
        lambda *_args: [{"Path": "附件/清单.txt", "Size": "20", "Packed Size": "10"}],
    )

    def fake_run(command, **_kwargs):
        output_dir = Path(next(value[2:] for value in command if value.startswith("-o")))
        target = output_dir / "附件" / "清单.txt"
        target.parent.mkdir(parents=True)
        target.write_text("项目名称：数字化建设", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(archive_module.subprocess, "run", fake_run)
    extracted = extract_archive_safely(source, tmp_path / "output")
    assert [path.name for path in extracted] == ["清单.txt"]
