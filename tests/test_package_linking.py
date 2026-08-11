from app.database import connect, init_db, now_text
from app.main import dashboard_context


def test_package_result_links_back_to_collected_notice(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "platform.sqlite3"))
    init_db()
    with connect() as db:
        db.execute(
            """INSERT INTO tenders(
                   fingerprint,source,title,url,published_date,notice_type,matched_terms,first_seen_at,source_item_id
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            ("fp", "国网", "数字化平台建设招标公告", "https://example.com/notice", "2026-08-12", "招标公告", "数字化", now_text(), "NOTICE-LINK"),
        )
        db.execute(
            """INSERT INTO sgcc_documents(sha256,original_name,size_bytes,source_url,notice_id,status,imported_at)
               VALUES(?,?,?,?,?,'processed',?)""",
            ("doc-sha", "标包清单.xlsx", 100, "https://example.com/attachment", "NOTICE-LINK", now_text()),
        )
        db.execute(
            """INSERT INTO sgcc_packages(
                   document_sha256,stable_key,notice_id,tender_no,package_no,project_name,package_name,
                   source_file,evidence,matched_terms,relevance_score,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("doc-sha", "stable", "NOTICE-LINK", "SGCC-01", "包1", "数字化平台建设", "软件实施标包", "标包清单.xlsx", "软件实施", "软件实施", 80, now_text()),
        )
    package = dashboard_context()["sgcc_packages"][0]
    assert package["notice_title"] == "数字化平台建设招标公告"
    assert package["notice_url"] == "https://example.com/notice"
    assert package["attachment_name"] == "标包清单.xlsx"
