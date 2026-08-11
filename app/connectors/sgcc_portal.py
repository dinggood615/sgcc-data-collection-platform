from __future__ import annotations

import json
import os
import time
from datetime import date
from urllib.parse import quote, unquote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.database import connect
from app.sgcc.pipeline import MAX_UPLOAD_BYTES, ingest_attachment


SOURCE_NAME = "国家电网电子商务平台"
SITE_URL = "https://ecp.sgcc.com.cn/ecp2.0/portal/#/list/list-spe/2018032600000014_5_2018032700291334"
API_URL = "https://ecp.sgcc.com.cn/ecp2.0/ecpwcmcore//index/noteList"
MENU_ID = "2018032700291334"
PAGE_SIZE = 100
MAX_PAGES = 100
DOWNLOAD_URL = "https://ecp.sgcc.com.cn/ecp2.0/ecpwcmcore//index/downLoadBid"


class RestrictedAttachment(PermissionError):
    """The portal requires an invited bidder account for this attachment."""


def _request_page(page: int) -> dict:
    payload = {
        "index": page,
        "size": PAGE_SIZE,
        "firstPageMenuId": MENU_ID,
        "purOrgStatus": "",
        "purOrgCode": "",
        "purType": "",
        "noticeType": "",
        "orgId": "",
        "key": "",
        "orgName": "",
    }
    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": "https://ecp.sgcc.com.cn/ecp2.0/portal/",
            "User-Agent": "Mozilla/5.0 (compatible; SGCCDataCollector/1.0; +https://github.com/dinggood615/sgcc-data-collection-platform)",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _detail_url(record: dict) -> str:
    document_type = str(record.get("doctype") or "doci-bid")
    # The detail API is keyed by noticeId. firstPageDocId looks plausible but
    # produces SYS001 for current notices, so it must not be used as the route id.
    document_id = record.get("noticeId") or record.get("id") or record.get("firstPageDocId")
    return f"https://ecp.sgcc.com.cn/ecp2.0/portal/#/doc/{document_type}/{document_id}_{MENU_ID}"


def _download_public_attachment_once(notice_id: str) -> tuple[str, bytes]:
    url = f"{DOWNLOAD_URL}?noticeId={quote(notice_id)}&noticeDetId="
    request = Request(url, headers={
        "Accept": "application/octet-stream,*/*",
        "Referer": "https://ecp.sgcc.com.cn/ecp2.0/portal/",
        "User-Agent": "Mozilla/5.0 (compatible; SGCCDataCollector/1.0; +https://github.com/dinggood615/sgcc-data-collection-platform)",
    })
    with urlopen(request, timeout=60) as response:
        content_type = (response.headers.get("Content-Type") or "").casefold()
        disposition = response.headers.get("Content-Disposition") or ""
        payload = response.read(MAX_UPLOAD_BYTES + 1)
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("公告附件为空或超过 100 MB")
    if "json" in content_type or payload[:1] in {b"{", b"["}:
        raise RestrictedAttachment("公告附件受邀账号权限保护")
    filename = "国网公告附件.zip"
    if "filename=" in disposition:
        filename = unquote(disposition.split("filename=", 1)[1].strip().strip('"')) or filename
    return filename, payload


def _download_public_attachment(notice_id: str) -> tuple[str, bytes]:
    """Retry temporary portal/network errors without retrying access restrictions."""
    attempts = max(1, min(int(os.getenv("ATTACHMENT_DOWNLOAD_RETRIES", "3")), 5))
    for attempt in range(1, attempts + 1):
        try:
            return _download_public_attachment_once(notice_id)
        except RestrictedAttachment:
            raise
        except HTTPError as exc:
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt == attempts:
                raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt == attempts:
                raise
        time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError("附件自动下载重试结束")


def _automatic_attachment_analysis(item: dict, keywords: list[str], exclusions: list[str]) -> tuple[str, str]:
    """Download and parse an attachment that the public detail page exposes.

    Invitation-only files are deliberately not downloaded because the website
    verifies the logged-in bidder list before allowing access.
    """
    if item["notice_type"] == "投标邀请书":
        return "", "restricted"
    notice_id = item["source_item_id"]
    filename, payload = _download_public_attachment(notice_id)
    result = ingest_attachment(filename, payload, notice_id, item["url"], keywords, exclusions)
    with connect() as db:
        matches = db.execute(
            """SELECT project_name,package_name,package_no,evidence,relevance_score
               FROM sgcc_packages WHERE notice_id=? AND relevance_score>=20
               ORDER BY relevance_score DESC,id LIMIT 5""",
            (notice_id,),
        ).fetchall()
    if not matches:
        return "", "" if result.status in {"processed", "duplicate", "no_text"} else "unavailable"
    evidence = []
    for match in matches:
        name = match["project_name"] or match["package_name"] or (f"包{match['package_no']}" if match["package_no"] else "附件命中")
        evidence.append(f"{name}（{match['relevance_score']}分）：{match['evidence']}")
    return "\n".join(evidence)[:4000], ""


def collect_sgcc_portal(target_date: str, keywords: list[str] | None = None, exclusions: list[str] | None = None) -> tuple[list[dict], str]:
    """Collect every SGCC notice for one date through the site's public API.

    Results are ordered newest first. Once a complete page is older than the
    target day, pagination stops, avoiding unnecessary load on the public site.
    """
    date.fromisoformat(target_date)
    items: list[dict] = []
    seen: set[str] = set()
    analysis_warnings: list[str] = []
    restricted_count = 0
    try:
        for page in range(1, MAX_PAGES + 1):
            payload = _request_page(page)
            if not payload.get("successful"):
                raise RuntimeError(payload.get("resultHint") or "SGCC API returned unsuccessful")
            records = payload.get("resultValue", {}).get("noteList") or []
            if not records:
                break
            page_dates = []
            for record in records:
                published = str(record.get("noticePublishTime") or "")[:10]
                if published:
                    page_dates.append(published)
                if published != target_date:
                    continue
                source_id = str(record.get("noticeId") or record.get("id") or record.get("firstPageDocId") or "")
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                notice_type = "投标邀请书" if str(record.get("noticeType")) == "100063008" else "招标公告"
                if str(record.get("doctype")) == "doci-change":
                    notice_type = "变更公告"
                title = str(record.get("title") or "").strip()
                code = str(record.get("code") or "").strip()
                organization = str(record.get("publishOrgName") or "").strip()
                excerpt = "；".join(part for part in (f"项目编号：{code}" if code else "", f"发布单位：{organization}" if organization else "") if part)
                item = {
                    "source": SOURCE_NAME,
                    "source_item_id": source_id,
                    "title": title,
                    "url": _detail_url(record),
                    "published_date": published,
                    "notice_type": notice_type,
                    "excerpt": excerpt,
                }
                if keywords:
                    try:
                        attachment_excerpt, attachment_warning = _automatic_attachment_analysis(item, keywords, exclusions or [])
                        if attachment_excerpt:
                            item["excerpt"] = "\n".join(part for part in (item["excerpt"], attachment_excerpt) if part)
                        if attachment_warning:
                            if attachment_warning == "restricted":
                                restricted_count += 1
                            elif attachment_warning == "unavailable":
                                analysis_warnings.append(f"{title}：附件已自动归类为暂不可解析")
                            else:
                                analysis_warnings.append(f"{title}：{attachment_warning}")
                        time.sleep(0.5)
                    except RestrictedAttachment:
                        restricted_count += 1
                    except Exception as exc:
                        analysis_warnings.append(f"{title}：自动重试后仍未取得附件（{type(exc).__name__}）")
                items.append(item)
            if page_dates and max(page_dates) < target_date:
                break
            total = int(payload.get("resultValue", {}).get("count") or 0)
            if page * PAGE_SIZE >= total:
                break
            time.sleep(0.8)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        return items, f"国网公开数据接口访问异常：{type(exc).__name__}；已保留本次成功读取的 {len(items)} 条记录"
    summaries: list[str] = []
    if restricted_count:
        summaries.append(f"{restricted_count} 个受邀权限附件已自动识别并跳过")
    if analysis_warnings:
        shown = "；".join(analysis_warnings[:3])
        if len(analysis_warnings) > 3:
            shown += f"；另有 {len(analysis_warnings) - 3} 个附件已记录自动处理原因"
        summaries.append(shown)
    return items, "；".join(summaries)
