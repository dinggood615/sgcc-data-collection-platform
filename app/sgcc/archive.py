from __future__ import annotations

import shutil
import json
import stat
import struct
import subprocess
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
    return extracted


def _seven_zip_executable() -> str | None:
    return shutil.which("7zz") or shutil.which("7z")


def _seven_zip_entries(archive_path: Path, output_dir: Path, limits: ArchiveLimits) -> list[dict[str, str]]:
    executable = _seven_zip_executable()
    if not executable:
        raise UnsafeArchive("未安装 7-Zip，无法安全解析 RAR/7Z 文件")
    completed = subprocess.run(
        [executable, "l", "-slt", "-p-", str(archive_path)],
        capture_output=True,
        timeout=60,
        check=False,
    )
    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    if completed.returncode != 0:
        if "password" in output.casefold() or "encrypted" in output.casefold():
            raise UnsafeArchive("压缩包已加密，请通过合法方式解密后重新导入")
        raise UnsafeArchive("RAR/7Z 文件无法读取或格式损坏")
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            if current.get("Path") and "Size" in current:
                entries.append(current)
            current = {}
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            current[key] = value
    if current.get("Path") and "Size" in current:
        entries.append(current)
    if len(entries) > limits.max_files:
        raise UnsafeArchive("压缩包文件数量超过限制")
    total = 0
    for entry in entries:
        if entry.get("Folder") == "+":
            continue
        name = entry["Path"]
        _safe_destination(output_dir, name)
        if entry.get("Encrypted") == "+":
            raise UnsafeArchive("压缩包已加密，请通过合法方式解密后重新导入")
        size = int(entry.get("Size") or 0)
        packed = int(entry.get("Packed Size") or 0)
        if size > limits.max_member_bytes:
            raise UnsafeArchive(f"单个文件过大：{name}")
        total += size
        if total > limits.max_total_bytes:
            raise UnsafeArchive("解压后总大小超过限制")
        if packed and size / packed > limits.max_ratio:
            raise UnsafeArchive(f"压缩比例异常：{name}")
    return entries


def _extract_seven_zip_safely(archive_path: Path, output_dir: Path, limits: ArchiveLimits) -> list[Path]:
    executable = _seven_zip_executable()
    _seven_zip_entries(archive_path, output_dir, limits)
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [executable, "x", "-y", "-bd", "-bb0", "-p-", f"-o{output_dir}", str(archive_path)],
        capture_output=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise UnsafeArchive("RAR/7Z 文件解包失败")
    extracted: list[Path] = []
    total = 0
    for candidate in output_dir.rglob("*"):
        if candidate.is_symlink():
            raise UnsafeArchive("压缩包包含符号链接")
        if not candidate.is_file():
            continue
        _safe_destination(output_dir, str(candidate.relative_to(output_dir)))
        total += candidate.stat().st_size
        if total > limits.max_total_bytes:
            raise UnsafeArchive("解压后总大小超过限制")
        extracted.append(candidate)
    return extracted


def _extract_rar_with_unar(archive_path: Path, output_dir: Path, limits: ArchiveLimits) -> list[Path]:
    lsar, unar = shutil.which("lsar"), shutil.which("unar")
    if not lsar or not unar:
        raise UnsafeArchive("当前 7-Zip 不支持该 RAR 版本，且未安装 unar")
    listed = subprocess.run([lsar, "-json", str(archive_path)], capture_output=True, timeout=60, check=False)
    if listed.returncode != 0:
        raise UnsafeArchive("RAR 文件无法读取、已加密或格式损坏")
    try:
        entries = json.loads(listed.stdout).get("lsarContents", [])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnsafeArchive("RAR 文件清单无法校验") from exc
    total = 0
    files = 0
    for entry in entries:
        if entry.get("XADIsDirectory"):
            continue
        files += 1
        name = str(entry.get("XADFileName") or "")
        _safe_destination(output_dir, name)
        if entry.get("XADIsEncrypted"):
            raise UnsafeArchive("压缩包已加密，请通过合法方式解密后重新导入")
        size = int(entry.get("XADFileSize") or 0)
        packed = int(entry.get("XADCompressedSize") or 0)
        total += size
        if size > limits.max_member_bytes or total > limits.max_total_bytes:
            raise UnsafeArchive("RAR 解压大小超过限制")
        if packed and size / packed > limits.max_ratio:
            raise UnsafeArchive(f"压缩比例异常：{name}")
    if files > limits.max_files:
        raise UnsafeArchive("压缩包文件数量超过限制")
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted = subprocess.run(
        [unar, "-force-overwrite", "-output-directory", str(output_dir), str(archive_path)],
        capture_output=True,
        timeout=180,
        check=False,
    )
    if extracted.returncode != 0:
        raise UnsafeArchive("RAR 文件解包失败")
    files: list[Path] = []
    actual_total = 0
    for path in output_dir.rglob("*"):
        if path.is_symlink():
            raise UnsafeArchive("压缩包包含符号链接")
        if path.is_file():
            _safe_destination(output_dir, str(path.relative_to(output_dir)))
            actual_total += path.stat().st_size
            if actual_total > limits.max_total_bytes:
                raise UnsafeArchive("解压后总大小超过限制")
            files.append(path)
    return files


def extract_archive_safely(archive_path: Path, output_dir: Path, limits: ArchiveLimits | None = None, depth: int = 0) -> list[Path]:
    """Safely extract ZIP, RAR and 7Z archives, including bounded nesting."""
    limits = limits or ArchiveLimits()
    if depth > limits.max_depth:
        raise UnsafeArchive("嵌套压缩层数超过限制")
    suffix = archive_path.suffix.casefold()
    if suffix == ".zip":
        extracted = extract_zip_safely(archive_path, output_dir, limits, depth=depth)
    elif suffix in {".rar", ".7z"}:
        try:
            extracted = _extract_seven_zip_safely(archive_path, output_dir, limits)
        except UnsafeArchive:
            if suffix != ".rar":
                raise
            extracted = _extract_rar_with_unar(archive_path, output_dir, limits)
    else:
        raise UnsafeArchive(f"不支持的压缩格式：{suffix or '未知'}")
    nested = [path for path in extracted if path.suffix.casefold() in {".zip", ".rar", ".7z"}]
    for nested_archive in nested:
        nested_dir = nested_archive.parent / f"{nested_archive.stem}-extracted"
        extracted.extend(extract_archive_safely(nested_archive, nested_dir, limits, depth + 1))
    return extracted
