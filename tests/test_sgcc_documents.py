from types import SimpleNamespace

from openpyxl import Workbook

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


def test_legacy_xls_is_converted_with_libreoffice(monkeypatch, tmp_path):
    source = tmp_path / "项目清单.xls"
    source.write_bytes(b"legacy")
    monkeypatch.setattr(documents.shutil, "which", lambda name: "/usr/bin/libreoffice" if name == "libreoffice" else None)

    def fake_run(command, **_kwargs):
        output_dir = __import__("pathlib").Path(command[command.index("--outdir") + 1])
        workbook = Workbook()
        workbook.active.append(["项目名称", "软件实施服务"])
        workbook.save(output_dir / "项目清单.xlsx")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(documents.subprocess, "run", fake_run)
    blocks, warning = documents.parse_document(source)
    assert not warning
    assert any("软件实施服务" in block.text for block in blocks)
    assert all(block.source_file == "项目清单.xls" for block in blocks)
    assert any("XLS→XLSX" in block.location for block in blocks)


def test_ofd_is_converted_to_pdf_before_parsing(monkeypatch, tmp_path):
    import fitz
    from easyofd import ofd as easyofd_module

    source = tmp_path / "电子文件.ofd"
    source.write_bytes(b"ofd")
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Digital software implementation project")
    pdf_bytes = pdf.tobytes()
    pdf.close()

    class FakeOFD:
        def read(self, _payload, **_kwargs):
            return None

        def to_pdf(self):
            return pdf_bytes

        def del_data(self):
            return None

    monkeypatch.setattr(easyofd_module, "OFD", FakeOFD)
    blocks, warning = documents.parse_document(source)
    assert not warning
    assert any("software implementation" in block.text for block in blocks)
    assert all(block.source_file == "电子文件.ofd" for block in blocks)
    assert any("OFD→PDF" in block.location for block in blocks)
