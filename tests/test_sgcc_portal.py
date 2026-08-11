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
