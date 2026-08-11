from types import SimpleNamespace

from app.sgcc import documents


class _Pixmap:
    def save(self, path):
        path.write_bytes(b"png")


class _Page:
    def get_pixmap(self, **_kwargs):
        return _Pixmap()


def test_scanned_page_uses_bounded_ocr(monkeypatch):
    monkeypatch.setattr(documents.shutil, "which", lambda _name: "/usr/bin/tesseract")
    monkeypatch.setattr(
        documents.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(stdout="项目名称：数字化平台建设".encode(), returncode=0),
    )
    block = documents._ocr_pdf_page(_Page(), "扫描附件.pdf", 2)
    assert block is not None
    assert block.text == "项目名称：数字化平台建设"
    assert block.location == "第 2 页（OCR）"
