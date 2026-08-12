from __future__ import annotations

import os
import sqlite3
import base64
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken


MIGRATION_SECRET_KEYS = (
    "admin_password",
    "smtp_auth_code",
    "wecom_corp_id",
    "wecom_callback_token",
    "wecom_encoding_aes_key",
    "wecom_webhook",
)
MIGRATION_MAX_BYTES = 100 * 1024 * 1024
KEYWORD_SEED_VERSION = "2026-08-11-v1"
CURATED_KEYWORDS = (
    "信息化", "信息化建设", "信息系统", "信息系统建设", "数字化", "数字化建设", "数字化转型",
    "软件开发", "软件实施", "系统实施", "系统开发", "应用开发", "平台开发", "二次开发", "定制开发",
    "系统集成", "系统建设", "平台建设", "应用系统", "系统改造", "系统升级", "国产化适配", "信创",
    "数据治理", "数据中台", "大数据", "数据分析", "数据服务", "人工智能", "云平台", "物联网",
    "网络安全", "数据安全", "信息安全", "技术服务", "运维服务", "信息系统运维", "软件运维",
    "人力外包", "人员外包", "技术外包", "IT外包", "软件外包", "研发外包", "驻场服务", "驻场开发",
    "人力资源服务", "劳务派遣",
)




def db_path() -> str:
    return os.getenv("DATABASE_PATH", "/data/platform.sqlite3")


@contextmanager
def connect():
    db = sqlite3.connect(db_path(), timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS keywords (
            term TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tenders (
            fingerprint TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
            url TEXT NOT NULL, published_date TEXT NOT NULL, notice_type TEXT NOT NULL,
            matched_terms TEXT NOT NULL, first_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
            finished_at TEXT, target_date TEXT NOT NULL, status TEXT NOT NULL,
            matched_count INTEGER NOT NULL DEFAULT 0, new_count INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS custom_sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            engine TEXT NOT NULL DEFAULT 'Fetcher',
            status TEXT NOT NULL DEFAULT '待自动适配',
            list_selector TEXT NOT NULL DEFAULT 'a',
            date_pattern TEXT NOT NULL DEFAULT '',
            profile_note TEXT NOT NULL DEFAULT '',
            profile_json TEXT NOT NULL DEFAULT '',
            builtin_code TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sgcc_documents (
            sha256 TEXT PRIMARY KEY,
            original_name TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            notice_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            imported_at TEXT NOT NULL,
            processed_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sgcc_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_sha256 TEXT NOT NULL,
            stable_key TEXT NOT NULL UNIQUE,
            notice_id TEXT NOT NULL DEFAULT '',
            tender_no TEXT NOT NULL DEFAULT '',
            package_no TEXT NOT NULL DEFAULT '',
            project_name TEXT NOT NULL DEFAULT '',
            package_name TEXT NOT NULL DEFAULT '',
            procurement_scope TEXT NOT NULL DEFAULT '',
            source_file TEXT NOT NULL,
            source_location TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            matched_terms TEXT NOT NULL DEFAULT '',
            rule_score INTEGER NOT NULL DEFAULT 0,
            model_name TEXT NOT NULL DEFAULT '',
            model_confidence INTEGER NOT NULL DEFAULT 0,
            model_reason TEXT NOT NULL DEFAULT '',
            model_category TEXT NOT NULL DEFAULT '',
            relevance_score INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(document_sha256) REFERENCES sgcc_documents(sha256)
        );
        CREATE INDEX IF NOT EXISTS idx_sgcc_packages_document ON sgcc_packages(document_sha256);
        CREATE INDEX IF NOT EXISTS idx_sgcc_packages_notice ON sgcc_packages(notice_id);
        CREATE INDEX IF NOT EXISTS idx_sgcc_documents_notice ON sgcc_documents(notice_id);
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(custom_sites)")}
        if "builtin_code" not in columns:
            db.execute("ALTER TABLE custom_sites ADD COLUMN builtin_code TEXT")
        if "profile_json" not in columns:
            db.execute("ALTER TABLE custom_sites ADD COLUMN profile_json TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_sites_builtin_code ON custom_sites(builtin_code) WHERE builtin_code IS NOT NULL")
        # Older releases used needs_review for every document without extracted
        # text. That state implied a manual task even when automatic processing
        # had completed, so normalize existing rows to the new machine category.
        db.execute("UPDATE sgcc_documents SET status='no_text' WHERE status='needs_review'")
        package_columns = {row["name"] for row in db.execute("PRAGMA table_info(sgcc_packages)")}
        for name, definition in (
            ("rule_score", "INTEGER NOT NULL DEFAULT 0"), ("model_name", "TEXT NOT NULL DEFAULT ''"),
            ("model_confidence", "INTEGER NOT NULL DEFAULT 0"), ("model_reason", "TEXT NOT NULL DEFAULT ''"),
            ("model_category", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in package_columns:
                db.execute(f"ALTER TABLE sgcc_packages ADD COLUMN {name} {definition}")
        # This edition is intentionally single-purpose. Remove generic/custom
        # sources left by older releases and keep exactly one managed SGCC source.
        db.execute("DELETE FROM custom_sites WHERE builtin_code IS NULL OR builtin_code<>?", ("sgcc_portal",))
        db.execute(
            """INSERT INTO custom_sites(name,url,enabled,engine,status,list_selector,date_pattern,profile_note,profile_json,builtin_code,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(url) DO UPDATE SET name=excluded.name,enabled=1,engine=excluded.engine,
               status=excluded.status,list_selector=excluded.list_selector,profile_note=excluded.profile_note,
               profile_json=excluded.profile_json,builtin_code=excluded.builtin_code""",
            (
                "国家电网招标公告及投标邀请书",
                "https://ecp.sgcc.com.cn/ecp2.0/portal/#/list/list-spe/2018032600000014_5_2018032700291334",
                1,
                "SGCC Public JSON API",
                "已适配（国网专用接口）",
                "$.resultValue.noteList",
                "noticePublishTime",
                "固定站点；自动按日期分页采集，无需人工验证。",
                '{"version":1,"mode":"sgcc_portal","menu_id":"2018032700291334","page_size":100}',
                "sgcc_portal",
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )
        tender_columns = {row["name"] for row in db.execute("PRAGMA table_info(tenders)")}
        for column, declaration in (
            ("source_item_id", "TEXT NOT NULL DEFAULT ''"),
            ("last_seen_at", "TEXT NOT NULL DEFAULT ''"),
            ("revision_hash", "TEXT NOT NULL DEFAULT ''"),
            ("relevance_score", "INTEGER NOT NULL DEFAULT 0"),
            ("relevance_level", "TEXT NOT NULL DEFAULT ''"),
            ("match_reason", "TEXT NOT NULL DEFAULT ''"),
            ("excerpt", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in tender_columns:
                db.execute(f"ALTER TABLE tenders ADD COLUMN {column} {declaration}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tenders_source_item ON tenders(source,source_item_id)")
        defaults = (
            ("admin_username", os.getenv("ADMIN_USERNAME", "admin")),
            ("schedule", ""), ("recipient", os.getenv("SMTP_TO", "")),
            ("smtp_host", os.getenv("SMTP_HOST", "")),
            ("smtp_port", os.getenv("SMTP_PORT", "")),
            ("smtp_user", os.getenv("SMTP_USER", "")),
            ("smtp_from", os.getenv("SMTP_FROM", "")),
            ("wecom_corp_id", os.getenv("WECOM_CORP_ID", "")),
            ("wecom_callback_token", os.getenv("WECOM_CALLBACK_TOKEN", "")),
            ("wecom_encoding_aes_key", os.getenv("WECOM_ENCODING_AES_KEY", "")),
            ("wecom_admin_users", os.getenv("WECOM_ADMIN_USERS", "")),
            ("wecom_public_url", os.getenv("WECOM_PUBLIC_URL", "")),
            ("wecom_webhook", os.getenv("WECOM_WEBHOOK", "")),
            ("wecom_push_enabled", os.getenv("WECOM_PUSH_ENABLED", "0")),
            ("wecom_push_message", ""),
            ("exclude_terms", ""),
        )
        for key, value in defaults:
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
        seed_version = db.execute("SELECT value FROM settings WHERE key='keyword_seed_version'").fetchone()
        if not seed_version or seed_version["value"] != KEYWORD_SEED_VERSION:
            # This version is an intentional baseline replacement. Recording the
            # version lets users clear or customize the list without it returning
            # on every application restart.
            db.execute("DELETE FROM keywords")
            db.executemany("INSERT INTO keywords(term,enabled) VALUES(?,1)", ((term,) for term in CURATED_KEYWORDS))
            db.execute(
                "INSERT INTO settings(key,value) VALUES('keyword_seed_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (KEYWORD_SEED_VERSION,),
            )


def backup_database(retention_days: int) -> Path:
    """Create a consistent SQLite backup and prune older platform backups."""
    retention_days = max(1, min(int(retention_days), 3650))
    source_path = Path(db_path())
    backup_dir = Path(os.getenv("BACKUP_DIR", str(source_path.parent / "backups")))
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"platform-{datetime.now().astimezone():%Y%m%d-%H%M%S}.sqlite3"
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    cutoff = datetime.now().astimezone().timestamp() - retention_days * 86400
    for candidate in backup_dir.glob("platform-*.sqlite3"):
        if candidate != target and candidate.stat().st_mtime < cutoff:
            candidate.unlink(missing_ok=True)
    return target


def reset_platform_state() -> Path:
    """Back up and atomically clear business data while preserving access and backup policy."""
    rollback = backup_database(int(setting("backup_retention_days", "14")))
    preserved_keys = ("admin_username", "admin_password", "backup_schedule", "backup_retention_days")
    with connect() as db:
        preserved = db.execute(
            f"SELECT key,value FROM settings WHERE key IN ({','.join('?' for _ in preserved_keys)})",
            preserved_keys,
        ).fetchall()
        for table in ("sgcc_packages", "sgcc_documents", "tenders", "runs", "keywords", "custom_sites"):
            db.execute(f"DELETE FROM {table}")
        db.execute("DELETE FROM sqlite_sequence WHERE name IN ('runs','custom_sites','sgcc_packages')")
        db.execute("DELETE FROM settings")
        db.executemany("INSERT INTO settings(key,value) VALUES(?,?)", ((row["key"], row["value"]) for row in preserved))
    init_db()
    return rollback


def export_migration_bundle() -> bytes:
    """Export a portable database plus secrets re-encrypted on the target host."""
    with tempfile.TemporaryDirectory(prefix="platform-export-") as temporary:
        snapshot = Path(temporary) / "platform.sqlite3"
        source = sqlite3.connect(db_path(), timeout=20)
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        manifest = {
            "format": "data-collection-platform-backup",
            "version": 1,
            "created_at": now_text(),
            "contains": ["settings", "sites", "keywords", "results", "runs"],
        }
        secrets = {key: setting(key, secret=True) for key in MIGRATION_SECRET_KEYS if setting(key, secret=True)}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "platform.sqlite3")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr("secrets.json", json.dumps(secrets, ensure_ascii=False))
        return output.getvalue()


def import_migration_bundle(payload: bytes) -> Path:
    """Validate and restore a portable bundle, keeping a rollback backup first."""
    if not payload or len(payload) > MIGRATION_MAX_BYTES:
        raise ValueError("备份文件为空或超过 100 MB。")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValueError("备份文件格式无效。") from exc
    with archive:
        names = set(archive.namelist())
        if names != {"platform.sqlite3", "manifest.json", "secrets.json"}:
            raise ValueError("备份文件内容不完整或包含未知文件。")
        if sum(item.file_size for item in archive.infolist()) > MIGRATION_MAX_BYTES:
            raise ValueError("备份解压后的内容超过 100 MB。")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "data-collection-platform-backup" or manifest.get("version") != 1:
            raise ValueError("备份版本不受支持。")
        secrets = json.loads(archive.read("secrets.json"))
        if not isinstance(secrets, dict) or any(key not in MIGRATION_SECRET_KEYS for key in secrets):
            raise ValueError("备份中的敏感配置无效。")
        database_bytes = archive.read("platform.sqlite3")
    rollback = backup_database(int(setting("backup_retention_days", "14")))
    with tempfile.NamedTemporaryFile(prefix="platform-import-", suffix=".sqlite3", delete=False) as temporary:
        temporary.write(database_bytes)
        source_path = Path(temporary.name)
    try:
        source = sqlite3.connect(source_path)
        try:
            quick_check = source.execute("PRAGMA quick_check").fetchone()
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"settings", "custom_sites", "keywords", "tenders", "runs"}
            if not quick_check or quick_check[0] != "ok" or not required.issubset(tables):
                raise ValueError("备份数据库校验失败。")
            destination = sqlite3.connect(db_path(), timeout=30)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        init_db()
        for key, value in secrets.items():
            set_setting(key, str(value), secret=True)
        set_setting("backup_message", f"已导入配置；导入前备份：{rollback.name}")
        return rollback
    finally:
        source_path.unlink(missing_ok=True)


def _cipher() -> Fernet:
    secret = os.getenv("APP_SECRET", "development-secret-change-me").encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def setting(key: str, default: str = "", secret: bool = False) -> str:
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    value = row["value"] if row else default
    if secret and value.startswith("enc:"):
        try:
            return _cipher().decrypt(value[4:].encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return ""
    return value


def set_setting(key: str, value: str, secret: bool = False) -> None:
    if secret:
        value = "enc:" + _cipher().encrypt(value.encode("utf-8")).decode("utf-8")
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
