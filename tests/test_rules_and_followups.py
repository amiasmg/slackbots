import base64
from types import SimpleNamespace as NS

import pytest

from funnel import app as funnel_app
from funnel.agent import run_agent
from funnel.config import Config
from funnel.intake import webhook
from funnel.models import Inbound
from funnel.tools import affinity
from tests.test_funnel import CFG, FakeResp, patch_requests

STAGE_LIST = {("GET", "/lists/7"): FakeResp(200, {"fields": [
    {"id": 1, "name": "Stage", "dropdown_options": [{"id": 11, "text": "Watchlist"}, {"id": 12, "text": "Diligence"}]}]})}


def test_new_entry_gets_watchlist_in_code(monkeypatch):
    calls = patch_requests(monkeypatch, {
        ("GET", "/organizations/5"): FakeResp(200, {"list_entries": []}),
        ("POST", "/lists/7/list-entries"): FakeResp(200, {"id": 99}),
        ("GET", "/field-values"): FakeResp(200, []),
        ("POST", "/field-values"): FakeResp(200, {}), **STAGE_LIST})
    out = affinity.affinity_add_to_pipeline(CFG, 5)
    assert out["added"] and out["stage"] == "Watchlist"
    assert ("POST", "https://api.affinity.co/field-values") in calls


def test_existing_entry_stage_untouched(monkeypatch):
    calls = patch_requests(monkeypatch, {
        ("GET", "/organizations/5"): FakeResp(200, {"list_entries": [{"id": 9, "list_id": 7}]})})
    assert affinity.affinity_add_to_pipeline(CFG, 5)["already_in_pipeline"]
    assert not any("field-values" in u or m == "POST" for m, u in calls)


def test_stage_failure_is_surfaced_not_hidden(monkeypatch):
    patch_requests(monkeypatch, {
        ("GET", "/organizations/5"): FakeResp(200, {"list_entries": []}),
        ("POST", "/lists/7/list-entries"): FakeResp(200, {"id": 99}),
        ("GET", "/lists/7"): FakeResp(200, {"fields": [{"id": 1, "name": "Stage", "dropdown_options": [
            {"id": 12, "text": "Diligence"}]}]})})  # no Watchlist option
    out = affinity.affinity_add_to_pipeline(CFG, 5)
    assert out["added"] and out["stage"] is None and "Watchlist" in out["stage_error"]["message"]


def test_agent_facing_setter_refuses_stage():
    out = affinity.affinity_set_list_entry_field(CFG, 3, 5, "stage", "Diligence")
    assert out["status"] == "request_error" and "automatically" in out["message"]


def test_scanned_pdf_is_attached_for_vision(monkeypatch):
    monkeypatch.setattr("funnel.agent.execute", lambda cfg, n, a, ctx=None: {
        "status": "ok", "needs_vision": True, "_pdf": b"%PDF-scan"})
    seen = []
    steps = iter([NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t", name="affinity_read_file",
                                                          input={"file_id": 1})]),
                  NS(stop_reason="end_turn", content=[NS(type="text", text="ok")])])
    run_agent(NS(messages=NS(create=lambda **kw: (seen.append(kw), next(steps))[1])), CFG, "x")
    last = seen[1]["messages"][-1]["content"]
    assert last[0]["type"] == "tool_result" and "_pdf" not in last[0]["content"]
    assert last[1]["type"] == "document"


def test_read_file_scanned_pdf_returns_vision_handoff(monkeypatch):
    from pypdf import PdfWriter
    import io
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.write(buf)
    patch_requests(monkeypatch, {("GET", "/entity-files/download/3"): FakeResp(
        200, content=buf.getvalue(), headers={"content-type": "application/pdf"})})
    out = affinity.affinity_read_file(CFG, 3, "scan.pdf")
    assert out["needs_vision"] and out["_pdf"][:4] == b"%PDF"


# ---- webhook attachments by URL ----

def test_webhook_rejects_http_and_private_urls():
    with pytest.raises(ValueError):
        webhook._check_host("http://example.com/x")
    with pytest.raises(ValueError):
        webhook._check_host("https://localhost/x")


def test_webhook_url_attachment_downloaded_and_pdf_sniffed(monkeypatch):
    monkeypatch.setattr(webhook, "_check_host", lambda url: None)
    resp = NS(is_redirect=False, headers={"content-type": "application/octet-stream"},
              raw=NS(read=lambda n, decode_content=True: b"%PDF-1.4 hi"), raise_for_status=lambda: None)
    monkeypatch.setattr(webhook.requests, "get", lambda *a, **k: resp)
    item = webhook.payload_to_inbound({"body": "hi", "attachments": ["https://files.zapier.com/abc"]})
    assert item.attachments[0].is_pdf and item.attachments[0].filename == "abc"


def test_webhook_attachment_failure_is_reported_to_agent():
    item = webhook.payload_to_inbound({"body": "hi", "attachments": ["http://insecure.example/x.pdf"]})
    assert not item.attachments and "could not be loaded" in item.text and "https" in item.text


# ---- thread follow-ups without @mention ----

class FakeBolt:
    handlers = {}

    def __init__(self, token=None):
        self.posted, self.thread = [], []
        self.client = NS(
            auth_test=lambda: {"user_id": "UBOT", "bot_id": "BBOT"},
            users_info=lambda user: {"user": {"real_name": "Pat", "profile": {"email": "pat@qed.com"}}},
            conversations_replies=lambda channel, ts, limit: {"messages": self.thread},
            chat_postMessage=lambda **kw: self.posted.append(kw))

    def event(self, name):
        def deco(fn):
            FakeBolt.handlers[name] = fn
            return fn
        return deco


def _bot(monkeypatch, reply="Added Sam as owner"):
    monkeypatch.setattr(funnel_app, "App", FakeBolt)
    llm = NS(messages=NS(create=lambda **kw: NS(stop_reason="end_turn", content=[NS(type="text", text=reply)])))
    app = funnel_app.build_app(Config(channel="C1"), llm)
    return app, FakeBolt.handlers["message"], FakeBolt.handlers["app_mention"]


def test_reply_in_our_thread_is_handled_without_mention(monkeypatch):
    app, on_message, _ = _bot(monkeypatch)
    app.thread = [{"ts": "1", "text": "acme.com", "user": "U1"}, {"ts": "2", "text": "summary", "bot_id": "BBOT"}]
    on_message({"channel": "C1", "ts": "3", "thread_ts": "1", "user": "U1", "text": "add Sam as owner"})
    assert app.posted and app.posted[0]["thread_ts"] == "1"


def test_reply_in_human_thread_is_ignored(monkeypatch):
    app, on_message, _ = _bot(monkeypatch)
    app.thread = [{"ts": "1", "text": "lunch?", "user": "U1"}, {"ts": "2", "text": "yes", "user": "U2"}]
    on_message({"channel": "C1", "ts": "3", "thread_ts": "1", "user": "U1", "text": "ok"})
    assert not app.posted


def test_chatter_in_our_thread_stays_silent_and_mention_not_double_handled(monkeypatch):
    app, on_message, on_mention = _bot(monkeypatch, reply="NO_REPLY")
    app.thread = [{"ts": "2", "text": "summary", "bot_id": "BBOT"}]
    on_message({"channel": "C1", "ts": "3", "thread_ts": "1", "user": "U1", "text": "thanks!"})
    assert not app.posted
    app2, on_message2, _ = _bot(monkeypatch)
    app2.thread = [{"ts": "2", "text": "summary", "bot_id": "BBOT"}]
    on_message2({"channel": "C1", "ts": "4", "thread_ts": "1", "user": "U1", "text": "<@UBOT> add Sam"})
    assert not app2.posted  # left to the app_mention event, so it is not answered twice
