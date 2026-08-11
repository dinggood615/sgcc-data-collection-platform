from __future__ import annotations

import hashlib
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app.database import connect, now_text
from app.matching import evaluate_relevance

from .archive import UnsafeArchive, extract_archive_safely
from .documents import TextBlock, parse_document


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
PACKAGE_MARKERS = ("包号", "包编号", "包名称", "标包名称", "项目名称", "工程名称", "需求名称", "分标名称", "采购范围", "项目概况", "服务名称", "服务内容")
STRONG_PACKAGE_MARKERS = ("包号", "包编号")
FIELD_PATTERNS = {
    "tender_no": re.compile(r"(?:分标编号|招标编号|采购编号)\s*[：:]?\s*([^|；;，,\s]{3,80})"),
    "package_no": re.compile(r"(?:包号|包编号)\s*[：:]?\s*([^|；;，,\s]{1,40})"),
    "project_name": re.compile(r"(?:项目名称|工程名称|需求名称)\s*[：:]?\s*([^|；;]{2,160})"),
    "package_name": re.compile(r"(?:包名称|标包名称|分标名称)\s*[：:]?\s*([^|；;]{2,160})"),
    "procurement_scope": re.compile(r"(?:采购范围|项目概况|服务内容|工作内容|技术规范)\s*[：:]?\s*([^|；;]{2,300})"),
}


@dataclass(frozen=True)
class ImportResult:
    sha256: str
    status: str
    package_count: int
    matched_count: int
    warnings: tuple[str, ...]


def _field(pattern_name: str, text: str) -> str:
    match = FIELD_PATTERNS[pattern_name].search(text)
    return " ".join(match.group(1).split()) if match else ""


def _candidate_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    """Join nearby paragraphs/rows that belong to one package.

    SGCC Word and PDF attachments often put package number, project name and
    scope on separate lines. Scoring each line independently loses the semantic
    relationship. Strong package markers define boundaries; a small context
    window supplies global tender metadata without merging the next package.
    """
    grouped: dict[str, list[TextBlock]] = {}
    for block in blocks:
        grouped.setdefault(block.source_file, []).append(block)
    candidates: list[TextBlock] = []
    for source_file, items in grouped.items():
        anchors = [index for index, block in enumerate(items) if any(marker in block.text for marker in STRONG_PACKAGE_MARKERS)]
        if not anchors:
            anchors = [index for index, block in enumerate(items) if any(marker in block.text for marker in PACKAGE_MARKERS)]
        if not anchors:
            candidates.extend(items)
            continue
        for position, anchor in enumerate(anchors):
            start = max(0, anchor - 2)
            next_anchor = anchors[position + 1] if position + 1 < len(anchors) else len(items)
            end = min(next_anchor, anchor + 5)
            selected = items[start:end]
            text = " | ".join(dict.fromkeys(block.text for block in selected if block.text))
            candidates.append(TextBlock(text, source_file, items[anchor].location))
    return candidates


def _evidence_excerpt(text: str, terms: tuple[str, ...], limit: int = 600) -> str:
    folded = text.casefold()
    positions = [folded.find(term.casefold()) for term in terms if term and folded.find(term.casefold()) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - limit // 3)
    excerpt = text[start:start + limit]
    return ("…" if start else "") + excerpt + ("…" if start + limit < len(text) else "")


def _parse_paths_parallel(paths: list[Path]) -> list[tuple[list[TextBlock], str]]:
    """Parse independent extracted files concurrently with a bounded worker pool."""
    if not paths:
        return []
    try:
        configured = int(os.getenv("ATTACHMENT_PARSE_WORKERS", "0"))
    except ValueError:
        configured = 0
    if configured <= 0:
        configured = os.cpu_count() or 2
    workers = max(1, min(configured, len(paths), 8))
    if workers == 1:
        return [parse_document(path) for path in paths]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sgcc-attachment") as executor:
        return list(executor.map(parse_document, paths))


def ingest_attachment(filename: str, payload: bytes, notice_id: str, source_url: str, keywords: list[str], exclusions: list[str]) -> ImportResult:
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("附件为空或超过 100 MB")
    safe_name = Path(filename).name
    digest = hashlib.sha256(payload).hexdigest()
    with connect() as db:
        existing = db.execute("SELECT status FROM sgcc_documents WHERE sha256=?", (digest,)).fetchone()
        if existing and existing["status"] == "processed":
            count = db.execute("SELECT COUNT(*) FROM sgcc_packages WHERE document_sha256=?", (digest,)).fetchone()[0]
            matched = db.execute("SELECT COUNT(*) FROM sgcc_packages WHERE document_sha256=? AND relevance_score>=20", (digest,)).fetchone()[0]
            return ImportResult(digest, "duplicate", count, matched, ("相同附件已经处理，本次未重复入库",))
        db.execute("""INSERT INTO sgcc_documents(sha256,original_name,size_bytes,source_url,notice_id,status,imported_at)
            VALUES(?,?,?,?,?,'processing',?) ON CONFLICT(sha256) DO UPDATE SET status='processing',message=''""",
            (digest, safe_name, len(payload), source_url.strip(), notice_id.strip(), now_text()))
    warnings: list[str] = []
    blocks: list[TextBlock] = []
    try:
        with tempfile.TemporaryDirectory(prefix="sgcc-import-") as temporary:
            root = Path(temporary)
            original = root / safe_name
            original.write_bytes(payload)
            paths = [original]
            if original.suffix.casefold() in {".zip", ".rar", ".7z"}:
                paths = extract_archive_safely(original, root / "extracted")
            parseable = [path for path in paths if path.suffix.casefold() not in {".zip", ".rar", ".7z"}]
            for parsed, warning in _parse_paths_parallel(parseable):
                blocks.extend(parsed)
                if warning:
                    warnings.append(warning)
        packages = []
        stable_keys: set[str] = set()
        for block in _candidate_blocks(blocks):
            relevance = evaluate_relevance(block.text, block.text, keywords, exclusions)
            fields = {name: _field(name, block.text) for name in FIELD_PATTERNS}
            stable_material = "\n".join((notice_id, fields["tender_no"], fields["package_no"], fields["project_name"], fields["package_name"], block.source_file, block.location))
            stable_key = hashlib.sha256(stable_material.encode("utf-8")).hexdigest()
            if stable_key in stable_keys:
                continue
            stable_keys.add(stable_key)
            evidence = _evidence_excerpt(block.text, relevance.terms)
            packages.append((digest, stable_key, notice_id.strip(), fields["tender_no"], fields["package_no"], fields["project_name"], fields["package_name"], fields["procurement_scope"], block.source_file, block.location, evidence, ",".join(relevance.terms), relevance.score, now_text()))
        with connect() as db:
            db.execute("DELETE FROM sgcc_packages WHERE document_sha256=?", (digest,))
            db.executemany("""INSERT OR REPLACE INTO sgcc_packages(document_sha256,stable_key,notice_id,tender_no,package_no,project_name,package_name,procurement_scope,source_file,source_location,evidence,matched_terms,relevance_score,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", packages)
            status = "processed" if blocks else "needs_review"
            message = "；".join(dict.fromkeys(warnings)) if warnings else ("解析完成" if blocks else "没有提取到可读文本，可能需要 OCR 或格式转换")
            db.execute("UPDATE sgcc_documents SET status=?,message=?,processed_at=? WHERE sha256=?", (status, message, now_text(), digest))
        matched = sum(1 for package in packages if package[-2] >= 20)
        return ImportResult(digest, status, len(packages), matched, tuple(dict.fromkeys(warnings)))
    except (UnsafeArchive, ValueError) as exc:
        with connect() as db:
            db.execute("UPDATE sgcc_documents SET status='rejected',message=?,processed_at=? WHERE sha256=?", (str(exc), now_text(), digest))
        raise
    except Exception as exc:
        with connect() as db:
            db.execute("UPDATE sgcc_documents SET status='failed',message=?,processed_at=? WHERE sha256=?", (type(exc).__name__, now_text(), digest))
        raise
