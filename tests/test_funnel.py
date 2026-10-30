from types import SimpleNamespace as NS

from funnel import tools
from funnel.agent import run_agent
from funnel.config import Config
from funnel.tools import affinity, base, pitchbook
from funnel.tools.urls import extract_company_urls


class FakeResp:
    def __init__(self, code=200, data=None, text=""):
        self.status_code, self._data, self.text = code, data, text
        self.ok = code < 400

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def patch_requests(monkeypatch, routes):
    calls = []

    def fake(method, url, **kw):
        calls.append((method, url))
        for (m, suffix), resp in routes.items():
            if m == method and url.endswith(suffix):
                return resp
        raise AssertionError(f"unrouted {method} {url}")

    monkeypatch.setattr(base.requests, "request", fake)
    return calls


CFG = Config(affinity_api_key="k", affinity_list_id=7, pitchbook_api_key="p", specter_api_key="s")


def test_url_extraction_fixes_slack_doubling_and_http():
    text = "look <http://www.acme.com/|http://www.acme.com/> and https://slack.com/x"
    assert extract_company_urls(text) == [{"url": "https://www.acme.com/", "domain": "acme.com"}]


def test_no_url():
    assert extract_company_urls("lunch?") == []


def test_pitchbook_distinguishes_auth_from_empty(monkeypatch):
    patch_requests(monkeypatch, {("GET", "/companies/search"): FakeResp(401, text="no")})
    assert pitchbook.pitchbook_lookup(CFG, "acme.com")["status"] == "auth_error"
    patch_requests(monkeypatch, {("GET", "/companies/search"): FakeResp(200, {"items": []})})
    assert pitchbook.pitchbook_lookup(CFG, "acme.com")["status"] == "empty"
    patch_requests(monkeypatch, {("GET", "/companies/search"): FakeResp(200, {"nope": 1})})
    assert pitchbook.pitchbook_lookup(CFG, "acme.com")["status"] == "bad_response"


def test_pitchbook_partial_failure_is_reported(monkeypatch):
    patch_requests(monkeypatch, {
        ("GET", "/companies/search"): FakeResp(200, {"items": [{"companyId": "1-A"}]}),
        ("GET", "/companies/1-A/bio"): FakeResp(200, {"description": "x"}),
        ("GET", "/companies/1-A/investors"): FakeResp(500, text="boom")})
    out = pitchbook.pitchbook_lookup(CFG, "acme.com")
    assert out["status"] == "ok" and out["bio"] and out["errors"][0]["section"] == "investors"


def test_add_to_pipeline_is_idempotent(monkeypatch):
    calls = patch_requests(monkeypatch, {
        ("GET", "/organizations/5"): FakeResp(200, {"list_entries": [{"id": 9, "list_id": 7}]})})
    out = affinity.affinity_add_to_pipeline(CFG, 5)
    assert out["already_in_pipeline"] and not any(m == "POST" for m, _ in calls)


def test_dry_run_never_writes(monkeypatch):
    calls = patch_requests(monkeypatch, {("GET", "/organizations/5"): FakeResp(200, {"list_entries": []})})
    cfg = Config(affinity_api_key="k", affinity_list_id=7, dry_run=True)
    assert affinity.affinity_add_to_pipeline(cfg, 5)["status"] == "dry_run"
    assert affinity.affinity_create_company(cfg, "A", "a.com")["status"] == "dry_run"
    assert not any(m == "POST" for m, _ in calls)


def test_missing_config_is_explicit():
    assert tools.execute(Config(), "pitchbook_lookup", {"domain": "a.com"})["status"] == "not_configured"


def test_tool_crash_is_contained(monkeypatch):
    monkeypatch.setitem(tools.BY_NAME, "pitchbook_lookup",
                        tools.Tool("pitchbook_lookup", "", {}, lambda cfg, domain: 1 / 0))
    assert tools.execute(CFG, "pitchbook_lookup", {"domain": "a.com"})["status"] == "tool_exception"


def test_agent_loop_runs_tools_then_answers(monkeypatch):
    monkeypatch.setattr("funnel.agent.execute", lambda cfg, n, a: {"status": "empty", "message": "none"})
    steps = iter([
        NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="affinity_find_company",
                                               input={"domain": "acme.com"})]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="*Acme* first touch")])])
    client = NS(messages=NS(create=lambda **kw: next(steps)))
    result = run_agent(client, CFG, "https://acme.com")
    assert result.text == "*Acme* first touch" and result.tool_calls[0]["tool"] == "affinity_find_company"


def test_no_reply_is_silent():
    client = NS(messages=NS(create=lambda **kw: NS(stop_reason="end_turn",
                                                   content=[NS(type="text", text="NO_REPLY")])))
    assert run_agent(client, CFG, "lunch?").silent
