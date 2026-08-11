from __future__ import annotations

import hashlib
import os
import smtplib
import ssl
import json
from urllib.request import Request, urlopen
from datetime import datetime
from email.message import EmailMessage

from .connectors.sgcc_portal import collect_sgcc_portal
from .database import connect, setting
from .emailing import normalize_recipients
from .matching import evaluate_relevance, parse_terms


def collect_enabled_sites(target_date: str, send_email: bool = True) -> tuple[int, int, str]:
    with connect() as db:
        keywords = [row["term"] for row in db.execute("SELECT term FROM keywords WHERE enabled=1")]
    if not keywords:
        return 0, 0, "尚未设置核心关键词，本次未访问采集站点"
    exclusions = parse_terms(setting("exclude_terms"))
    items, notices = [], []
    sgcc_items, sgcc_notice = collect_sgcc_portal(target_date)
    items.extend(sgcc_items)
    if sgcc_notice:
        notices.append(sgcc_notice)
    ranked_items = []
    for item in items:
        if "relevance_score" not in item:
            relevance = evaluate_relevance(item["title"], item.get("excerpt", ""), keywords, exclusions)
            if relevance.score < 20:
                continue
            item.update(relevance_score=relevance.score, relevance_level=relevance.level,
                        match_reason="；".join(relevance.reasons), excerpt="")
            item["matched_terms"] = relevance.terms
        ranked_items.append(item)
    items = ranked_items
    new_items = []
    with connect() as db:
        for item in items:
            source_item_id = item.get("source_item_id", "")
            identity = source_item_id or f"{item['title']}\n{item['url']}\n{item['published_date']}"
            fingerprint = hashlib.sha256(f"{item['source']}\n{identity}".encode()).hexdigest()
            revision_hash = hashlib.sha256(f"{item['title']}\n{item.get('excerpt', '')}\n{item['published_date']}".encode()).hexdigest()
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            existed = db.execute("SELECT 1 FROM tenders WHERE fingerprint=?", (fingerprint,)).fetchone() is not None
            db.execute("""INSERT INTO tenders(fingerprint,source,title,url,published_date,notice_type,matched_terms,first_seen_at,relevance_score,relevance_level,match_reason,excerpt,source_item_id,last_seen_at,revision_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(fingerprint) DO UPDATE SET title=excluded.title,url=excluded.url,published_date=excluded.published_date,
                notice_type=excluded.notice_type,matched_terms=excluded.matched_terms,relevance_score=excluded.relevance_score,
                relevance_level=excluded.relevance_level,match_reason=excluded.match_reason,excerpt=excluded.excerpt,
                source_item_id=excluded.source_item_id,last_seen_at=excluded.last_seen_at,revision_hash=excluded.revision_hash""",
                (fingerprint, item["source"], item["title"], item["url"], item["published_date"], item["notice_type"],
                 ",".join(item["matched_terms"]), now, item.get("relevance_score", 0), item.get("relevance_level", ""),
                 item.get("match_reason", ""), item.get("excerpt", ""), source_item_id, now, revision_hash))
            if not existed:
                new_items.append(item)
        report_items = [dict(row) for row in db.execute(
            "SELECT * FROM tenders WHERE published_date=? ORDER BY relevance_score DESC, first_seen_at DESC",
            (target_date,),
        ).fetchall()]
    for item in report_items:
        item["matched_terms"] = [term for term in item["matched_terms"].split(",") if term]
    recipient_value = setting("recipient")
    smtp = {"host": setting("smtp_host", os.getenv("SMTP_HOST", "smtp.163.com")), "port": setting("smtp_port", os.getenv("SMTP_PORT", "465")), "user": setting("smtp_user", os.getenv("SMTP_USER", "")), "from": setting("smtp_from", os.getenv("SMTP_FROM", "")), "auth_code": setting("smtp_auth_code", os.getenv("SMTP_AUTH_CODE", ""), secret=True)}
    if send_email and recipient_value and smtp["user"] and smtp["auth_code"]:
        try:
            recipients = normalize_recipients(recipient_value)
            send_report(recipients, target_date, report_items, notices, smtp)
        except ValueError:
            notices.append("收件邮箱配置无效，请在邮件与定时中重新保存。")
    return len(items), len(new_items), "; ".join(notices) or "采集完成"


def send_report(recipients: list[str], target_date: str, report_items: list[dict], notices: list[str], smtp_config: dict[str, str]) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"招标采集日报 {target_date}（共 {len(report_items)} 条）"
    msg["From"] = smtp_config["from"] or smtp_config["user"]
    msg["To"] = ", ".join(recipients)
    lines = [f"目标日期：{target_date}", f"前一自然日命中结果：{len(report_items)} 条"]
    for item in report_items:
        lines.extend(("", f"[{item.get('relevance_level', '相关')} {item.get('relevance_score', 0)}分] {item['title']}", f"来源：{item['source']}；匹配：{','.join(item['matched_terms'])}", item.get("match_reason", ""), item["url"]))
    if notices:
        lines.extend(("", "提示：", *notices))
    msg.set_content("\n".join(lines))
    with smtplib.SMTP_SSL(smtp_config["host"], int(smtp_config["port"]), context=ssl.create_default_context()) as smtp:
        smtp.login(smtp_config["user"], smtp_config["auth_code"])
        smtp.send_message(msg, to_addrs=recipients)


def build_wecom_report(target_date: str, matched: int, new_items: list[dict], notices: list[str]) -> str:
    lines = [f"数据采集日报 {target_date}", f"关键词命中：{matched} 条｜新增：{len(new_items)} 条"]
    for item in new_items[:10]:
        lines.extend(("", f"[{item.get('relevance_level', '相关')} {item.get('relevance_score', 0)}分] {item['title'][:120]}", item.get("match_reason", ""), item["url"]))
    if len(new_items) > 10:
        lines.append(f"另有 {len(new_items) - 10} 条结果，请登录平台查看。")
    if notices:
        lines.extend(("", "提示：", *notices[:3]))
    return "\n".join(lines)


def send_wecom_robot_message(webhook: str, text: str) -> None:
    payload = json.dumps({"msgtype": "text", "text": {"content": text[:4000]}}, ensure_ascii=False).encode("utf-8")
    request = Request(webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    if body.get("errcode") != 0:
        raise RuntimeError(f"wechat error {body.get('errcode')}")
