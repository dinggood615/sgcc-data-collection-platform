from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.sgcc.archive import UnsafeArchive, extract_zip_safely


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
