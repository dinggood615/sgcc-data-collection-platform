from app.connectors import sgcc_portal


def test_collects_every_matching_notice_and_stops_after_older_page(monkeypatch):
    calls = []

    def fake_page(page):
        calls.append(page)
        if page == 1:
            records = [
                {"noticeId": 11, "firstPageDocId": 91, "title": "信息化服务招标", "noticePublishTime": "2026-08-10", "doctype": "doci-bid", "code": "A1", "publishOrgName": "国网甲"},
                {"noticeId": 12, "firstPageDocId": 92, "title": "软件开发邀请", "noticePublishTime": "2026-08-10", "doctype": "doci-bid", "noticeType": 100063008},
            ]
        else:
            records = [{"noticeId": 10, "title": "旧公告", "noticePublishTime": "2026-08-09", "doctype": "doci-bid"}]
        return {"successful": True, "resultValue": {"count": 300, "noteList": records}}

    monkeypatch.setattr(sgcc_portal, "_request_page", fake_page)
    monkeypatch.setattr(sgcc_portal.time, "sleep", lambda _: None)
    items, warning = sgcc_portal.collect_sgcc_portal("2026-08-10")
    assert warning == ""
    assert calls == [1, 2]
    assert len(items) == 2
    assert items[0]["source_item_id"] == "11"
    assert "/doc/doci-bid/11_2018032700291334" in items[0]["url"]
    assert items[1]["notice_type"] == "投标邀请书"


def test_returns_partial_results_when_later_page_fails(monkeypatch):
    def fake_page(page):
        if page == 1:
            return {"successful": True, "resultValue": {"count": 300, "noteList": [
                {"noticeId": 11, "title": "信息化服务招标", "noticePublishTime": "2026-08-10", "doctype": "doci-bid"}
            ]}}
        raise TimeoutError("slow")

    monkeypatch.setattr(sgcc_portal, "_request_page", fake_page)
    monkeypatch.setattr(sgcc_portal.time, "sleep", lambda _: None)
    items, warning = sgcc_portal.collect_sgcc_portal("2026-08-10")
    assert len(items) == 1
    assert "已保留本次成功读取的 1 条记录" in warning


def test_automatic_attachment_evidence_is_used_for_matching(monkeypatch):
    monkeypatch.setattr(sgcc_portal, "_request_page", lambda _page: {
        "successful": True,
        "resultValue": {"count": 1, "noteList": [{
            "noticeId": 11,
            "title": "某服务类公开招标采购",
            "noticePublishTime": "2026-08-10",
            "doctype": "doci-bid",
        }]},
    })
    monkeypatch.setattr(
        sgcc_portal,
        "_automatic_attachment_analysis",
        lambda _item, _keywords, _exclusions: ("包1：调度管理系统软件开发与实施服务", ""),
    )
    items, warning = sgcc_portal.collect_sgcc_portal("2026-08-10", ["软件开发"], [])
    assert warning == ""
    assert "软件开发" in items[0]["excerpt"]


def test_invitation_attachment_respects_access_control():
    item = {"notice_type": "投标邀请书", "source_item_id": "11", "url": "https://example.com"}
    excerpt, warning = sgcc_portal._automatic_attachment_analysis(item, ["软件开发"], [])
    assert excerpt == ""
    assert "未尝试绕过访问控制" in warning
