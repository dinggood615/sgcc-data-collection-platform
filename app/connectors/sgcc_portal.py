from __future__ import annotations

import json
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SOURCE_NAME = "国家电网电子商务平台"
SITE_URL = "https://ecp.sgcc.com.cn/ecp2.0/portal/#/list/list-spe/2018032600000014_5_2018032700291334"
API_URL = "https://ecp.sgcc.com.cn/ecp2.0/ecpwcmcore//index/noteList"
MENU_ID = "2018032700291334"
PAGE_SIZE = 100
MAX_PAGES = 100


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


def collect_sgcc_portal(target_date: str) -> tuple[list[dict], str]:
    """Collect every SGCC notice for one date through the site's public API.

    Results are ordered newest first. Once a complete page is older than the
    target day, pagination stops, avoiding unnecessary load on the public site.
    """
    date.fromisoformat(target_date)
    items: list[dict] = []
    seen: set[str] = set()
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
                items.append({
                    "source": SOURCE_NAME,
                    "source_item_id": source_id,
                    "title": title,
                    "url": _detail_url(record),
                    "published_date": published,
                    "notice_type": notice_type,
                    "excerpt": excerpt,
                })
            if page_dates and max(page_dates) < target_date:
                break
            total = int(payload.get("resultValue", {}).get("count") or 0)
            if page * PAGE_SIZE >= total:
                break
            time.sleep(0.8)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        return items, f"国网公开数据接口访问异常：{type(exc).__name__}；已保留本次成功读取的 {len(items)} 条记录"
    return items, ""
