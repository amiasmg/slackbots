import base64
import json
from types import SimpleNamespace as NS

from funnel.agent import run_agent
from funnel.config import Config
from funnel.intake import graphmail, webhook
from funnel.models import Attachment, Inbound
from funnel.textutil import html_to_text
from funnel.tools import affinity, base, pitchbook, specter
from tests.test_funnel import CFG, FakeResp, patch_requests


def test_html_to_text_keeps_links():
    out = html_to_text("<div>Hi<br>see <a href='https://acme.com'>our site</a></div><script>x()</script>")
    assert "https://acme.com" in out and "x()" not in out


def test_cache_avoids_second_paid_call(monkeypatch, tmp_path):
    cfg = Config(specter_api_key="s", cache_dir=str(tmp_path))
    calls = patch_requests(monkeypatch, {("POST", "/companies"): FakeResp(200, [{"name": "Acme"}])})
    assert specter.specter_lookup(cfg, "acme.com")["status"] == "ok"
    again = specter.specter_lookup(cfg, "acme.com")
    assert again["cached"] and len(calls) == 1


def test_errors_are_not_cached(monkeypatch, tmp_path):
    cfg = Config(specter_api_key="s", cache_dir=str(tmp_path))
    calls = patch_requests(monkeypatch, {("POST", "/companies"): FakeResp(500, text="x")})
    specter.specter_lookup(cfg, "acme.com")
    specter.specter_lookup(cfg, "acme.com")
    assert len(calls) == 2


def test_pitchbook_only_requests_needed_sections(monkeypatch):
    calls = patch_requests(monkeypatch, {
        ("GET", "/companies/search"): FakeResp(200, {"items": [{"companyId": "1-A"}]}),
        ("GET", "/companies/1-A/investors"): FakeResp(200, []),
        ("GET", "/companies/1-A/deals"): FakeResp(200, [])})
    pitchbook.pitchbook_lookup(CFG, "acme.com")
    assert not any(u.endswith("/bio") for _, u in calls)


def test_find_internal_user_filters_external(monkeypatch):
    patch_requests(monkeypatch, {("GET", "/persons"): FakeResp(200, {"persons": [
        {"id": 1, "type": 0, "first_name": "Sam", "last_name": "Founder"},
        {"id": 2, "type": 1, "first_name": "Sam", "last_name": "Partner", "emails": ["sam@qed.com"]}]})})
    out = affinity.affinity_find_internal_user(CFG, "Sam")
    assert [m["id"] for m in out["matches"]] == [2] and not out["ambiguous"]


def test_attach_inbound_file_uploads_once(monkeypatch):
    ctx = Inbound("email", "x", attachments=[Attachment("deck.pdf", "application/pdf", b"%PDF")])
    calls = patch_requests(monkeypatch, {
        ("GET", "/entity-files"): FakeResp(200, {"entity_files": []}),
        ("POST", "/entity-files"): FakeResp(200, {})})
    assert affinity.affinity_attach_inbound_file(CFG, ctx, 5, 0)["uploaded"]
    patch_requests(monkeypatch, {("GET", "/entity-files"): FakeResp(200, {"entity_files": [
        {"id": 1, "name": "deck.pdf", "size": 4}]})})
    assert affinity.affinity_attach_inbound_file(CFG, ctx, 5, 0)["already_filed"]
    assert affinity.affinity_attach_inbound_file(CFG, ctx, 5, 3)["status"] == "request_error"


def test_read_file_extracts_text_and_rejects_unknown_types(monkeypatch):
    patch_requests(monkeypatch, {("GET", "/entity-files/download/9"): FakeResp(
        200, content=b"ARR $4M in Q3", headers={"content-type": "text/plain"})})
    assert "ARR $4M" in affinity.affinity_read_file(CFG, 9, "memo.txt")["text"]
    patch_requests(monkeypatch, {("GET", "/entity-files/download/9"): FakeResp(
        200, content=b"zz", headers={"content-type": "application/zip"})})
    assert affinity.affinity_read_file(CFG, 9, "a.zip")["status"] == "unsupported"


def test_agent_sends_pdf_as_document_and_handles_pause_turn():
    seen = []
    steps = iter([NS(stop_reason="pause_turn", content=[NS(type="text", text="")]),
                  NS(stop_reason="end_turn", content=[NS(type="text", text="done")])])

    def create(**kw):
        seen.append(kw)
        return next(steps)

    item = Inbound("email", "see deck", "Pat <pat@qed.com>", "Acme deck",
                   [Attachment("deck.pdf", "application/pdf", b"%PDF-1"),
                    Attachment("m.xlsx", "application/vnd.ms-excel", b"x")])
    out = run_agent(NS(messages=NS(create=create)), CFG, item)
    content = seen[0]["messages"][0]["content"]
    assert out.text == "done" and content[0]["type"] == "document"
    assert "[1] m.xlsx" in content[-1]["text"] and "not readable" in content[-1]["text"]
    assert any(t.get("name") == "web_search" for t in seen[0]["tools"])


def _graph_routes(msg_extra=None):
    msg = {"id": "m1", "subject": "Acme", "from": {"emailAddress": {"name": "Pat", "address": "pat@qed.com"}},
           "body": {"contentType": "text", "content": "https://acme.com"}, "hasAttachments": True}
    return {
        ("POST", "/oauth2/v2.0/token"): FakeResp(200, {"access_token": "t"}),
        ("GET", "/mailFolders/inbox/messages"): FakeResp(200, {"value": [msg]}),
        ("GET", "/messages/m1/attachments"): FakeResp(200, {"value": [
            {"@odata.type": "#microsoft.graph.fileAttachment", "name": "deck.pdf", "contentType": "application/pdf",
             "size": 4, "contentBytes": base64.b64encode(b"%PDF").decode()},
            {"@odata.type": "#microsoft.graph.fileAttachment", "name": "logo.png", "isInline": True,
             "size": 1, "contentBytes": "AA=="}]}),
        ("PATCH", "/messages/m1"): FakeResp(200, {}), **(msg_extra or {})}


def test_graph_poll_processes_and_marks_read(monkeypatch):
    cfg = Config(ms_tenant_id="t", ms_client_id="c", ms_client_secret="s", mailbox="qed.topfunnel@x.com")
    calls = patch_requests(monkeypatch, _graph_routes())
    got = []
    assert graphmail.poll_once(cfg, lambda item: got.append(item) or True, {}) == 1
    item = got[0]
    assert item.sender == "Pat <pat@qed.com>" and [a.filename for a in item.attachments] == ["deck.pdf"]
    assert any(m == "PATCH" for m, _ in calls)


def test_graph_failure_retries_then_gives_up(monkeypatch):
    cfg = Config(ms_tenant_id="t", ms_client_id="c", ms_client_secret="s", mailbox="m@x.com")
    calls = patch_requests(monkeypatch, _graph_routes())
    failures = {}
    for _ in range(2):  # first two failures: leave unread
        assert graphmail.poll_once(cfg, lambda item: False, failures) == 0
    assert not any(m == "PATCH" for m, _ in calls)
    assert graphmail.poll_once(cfg, lambda item: False, failures) == 1  # third: give up, mark read
    assert any(m == "PATCH" for m, _ in calls)


def test_graph_auth_failure_is_reported_not_swallowed(monkeypatch, caplog):
    cfg = Config(ms_tenant_id="t", ms_client_id="c", ms_client_secret="bad")
    patch_requests(monkeypatch, {("POST", "/oauth2/v2.0/token"): FakeResp(401, text="no")})
    assert graphmail.poll_once(cfg, lambda i: True, {}) == 0
    assert "auth" in caplog.text.lower()


def test_webhook_payload_and_secret():
    item = webhook.payload_to_inbound({"from": "Pat <p@q.com>", "subject": "S", "body": "<div>hi</div>",
                                       "attachments": [{"filename": "d.pdf", "content_type": "application/pdf",
                                                        "content_base64": base64.b64encode(b"x").decode()}]})
    assert item.text == "hi" and item.attachments[0].data == b"x" and item.source == "email"

    import threading
    from http.server import ThreadingHTTPServer
    from urllib import error, request
    cfg = Config(webhook_secret="sek")
    got, evt = [], threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), webhook.make_handler(cfg, lambda i: (got.append(i), evt.set())))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/inbound/email"
    body = json.dumps({"from": "a", "subject": "s", "body": "b"}).encode()
    try:
        request.urlopen(request.Request(url, body, {"X-Funnel-Secret": "wrong"}))
        assert False, "should have been rejected"
    except error.HTTPError as e:
        assert e.code == 401
    assert request.urlopen(request.Request(url, body, {"X-Funnel-Secret": "sek"})).status == 202
    assert evt.wait(2) and got[0].subject == "s"
    server.shutdown()
