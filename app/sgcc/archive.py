from __future__ import annotations

import shutil
import stat
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArchiveLimits:
    max_files: int = 500
    max_member_bytes: int = 80 * 1024 * 1024
    max_total_bytes: int = 300 * 1024 * 1024
    max_ratio: int = 200
    max_depth: int = 2


class UnsafeArchive(ValueError):
    pass


BLOCKED_SUFFIXES = {
    ".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".sh", ".js", ".jar",
    ".msi", ".scr", ".vbs", ".lnk",
}


def _safe_destination(root: Path, name: str) -> Path:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or "\x00" in normalized:
        raise UnsafeArchive("压缩包包含非法绝对路径")
    target = (root / normalized).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise UnsafeArchive("压缩包包含路径穿越内容")
    if target.suffix.casefold() in BLOCKED_SUFFIXES:
        raise UnsafeArchive(f"压缩包包含禁止执行的文件：{target.name}")
    return target


def _local_member_names(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> tuple[str, str]:
    """Read the authoritative local-header name for malformed SGCC ZIPs."""
    if archive.fp is None:
        return member.filename, member.orig_filename
    position = archive.fp.tell()
    try:
        archive.fp.seek(member.header_offset)
        header = archive.fp.read(zipfile.sizeFileHeader)
        fields = struct.unpack(zipfile.structFileHeader, header)
        if fields[0] != zipfile.stringFileHeader:
            raise UnsafeArchive("ZIP 文件头无效")
        raw_name = archive.fp.read(fields[zipfile._FH_FILENAME_LENGTH])
        utf8_flag = bool(fields[zipfile._FH_GENERAL_PURPOSE_FLAG_BITS] & zipfile._MASK_UTF_FILENAME)
        expected_name = raw_name.decode("utf-8" if utf8_flag else (archive.metadata_encoding or "cp437"))
        for encoding in ("utf-8", "gb18030", "cp437"):
            try:
                return raw_name.decode(encoding), expected_name
            except UnicodeDecodeError:
                continue
        return member.filename, expected_name
    finally:
        archive.fp.seek(position)


def extract_zip_safely(archive_path: Path, output_dir: Path, limits: ArchiveLimits | None = None, depth: int = 0) -> list[Path]:
    limits = limits or ArchiveLimits()
    if depth > limits.max_depth:
        raise UnsafeArchive("嵌套压缩层数超过限制")
    extracted: list[Path] = []
    total = 0
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise UnsafeArchive("不是有效的 ZIP 文件") from exc
    with archive:
        if len(archive.infolist()) > limits.max_files:
            raise UnsafeArchive("压缩包文件数量超过限制")
        for member in archive.infolist():
            local_name, expected_name = _local_member_names(archive, member)
            # Some SGCC archives use GBK in the central directory but UTF-8 in
            # the local header. Align ZipInfo with the local header so Python's
            # integrity check succeeds; all path-safety checks still apply.
            member.filename = local_name
            member.orig_filename = expected_name
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UnsafeArchive("压缩包包含符号链接")
            if member.flag_bits & 0x1:
                raise UnsafeArchive("压缩包已加密，请通过合法方式解密后重新导入")
            if member.file_size > limits.max_member_bytes:
                raise UnsafeArchive(f"单个文件过大：{member.filename}")
            total += member.file_size
            if total > limits.max_total_bytes:
                raise UnsafeArchive("解压后总大小超过限制")
            if member.compress_size and member.file_size / member.compress_size > limits.max_ratio:
                raise UnsafeArchive(f"压缩比例异常：{member.filename}")
            target = _safe_destination(output_dir, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            extracted.append(target)
    nested = [path for path in extracted if path.suffix.casefold() == ".zip"]
    for nested_archive in nested:
        nested_dir = nested_archive.parent / f"{nested_archive.stem}-extracted"
        nested_dir.mkdir(exist_ok=True)
        extracted.extend(extract_zip_safely(nested_archive, nested_dir, limits, depth + 1))
    return extracted
