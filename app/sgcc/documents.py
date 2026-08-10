from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from openpyxl import load_workbook


@dataclass(frozen=True)
class TextBlock:
    text: str
    source_file: str
    location: str


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


def _excel_blocks(path: Path) -> list[TextBlock]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    blocks: list[TextBlock] = []
    try:
        for sheet in workbook.worksheets:
            headers: list[str] = []
            for row_number, row in enumerate(sheet.iter_rows(values_only=True), 1):
                cells = [_clean(cell) for cell in row]
                if not headers and any(name in cell for cell in cells for name in ("项目名称", "包号", "包名称", "采购范围", "分标编号")):
                    headers = cells
                    text = " | ".join(filter(None, cells))
                elif headers:
                    text = " | ".join(
                        f"{headers[index] or f'字段{index + 1}'}：{value}"
                        for index, value in enumerate(cells)
                        if value
                    )
                else:
                    text = " | ".join(filter(None, cells))
                if text:
                    blocks.append(TextBlock(text, path.name, f"工作表“{sheet.title}”第 {row_number} 行"))
    finally:
        workbook.close()
    return blocks


def _word_blocks(path: Path) -> list[TextBlock]:
    document = Document(path)
    blocks = [TextBlock(_clean(p.text), path.name, f"段落 {index}") for index, p in enumerate(document.paragraphs, 1) if _clean(p.text)]
    for table_number, table in enumerate(document.tables, 1):
        for row_number, row in enumerate(table.rows, 1):
            text = " | ".join(_clean(cell.text) for cell in row.cells if _clean(cell.text))
            if text:
                blocks.append(TextBlock(text, path.name, f"表格 {table_number} 第 {row_number} 行"))
    return blocks


def _pdf_blocks(path: Path) -> list[TextBlock]:
    import fitz

    blocks: list[TextBlock] = []
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, 1):
            text = _clean(page.get_text("text"))
            if text:
                blocks.append(TextBlock(text, path.name, f"第 {page_number} 页"))
    return blocks


def _text_blocks(path: Path) -> list[TextBlock]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    return [TextBlock(_clean(line), path.name, f"第 {index} 行") for index, line in enumerate(text.splitlines(), 1) if _clean(line)]


def parse_document(path: Path) -> tuple[list[TextBlock], str]:
    suffix = path.suffix.casefold()
    try:
        if suffix == ".xlsx":
            return _excel_blocks(path), ""
        if suffix == ".docx":
            return _word_blocks(path), ""
        if suffix == ".pdf":
            blocks = _pdf_blocks(path)
            return blocks, "" if blocks else "PDF 可能是扫描件，需要 OCR"
        if suffix in {".txt", ".csv"}:
            return _text_blocks(path), ""
        if suffix in {".xls", ".doc", ".ofd"}:
            return [], f"{suffix} 需要 LibreOffice/OFD 转换后解析"
        return [], ""
    except Exception as exc:
        return [], f"{path.name} 解析失败：{type(exc).__name__}"
