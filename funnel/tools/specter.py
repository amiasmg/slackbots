"""Specter lookup, used when Pitchbook is empty/failing or to cross-check.

ASSUMPTION: endpoint/auth below follow Specter's public enrichment API
(POST /api/v1/companies with a list of {"domain"}, X-API-Key header). Not verified
against a live key - confirm before relying on it.
"""
from .base import api_call, cached, empty, error, ok

BASE = "https://app.tryspecter.com/api/v1"


def specter_lookup(cfg, domain: str) -> dict:
    if not cfg.specter_api_key:
        return error("not_configured", "SPECTER_API_KEY is not set")
    return cached(cfg, f"specter:{domain}", lambda: _fetch(cfg, domain))


def _fetch(cfg, domain: str) -> dict:
    # Specter bills for the whole record per call, so this runs once per domain per cache window.
    res = api_call("Specter", "POST", f"{BASE}/companies",
                   json=[{"domain": domain}], headers={"X-API-Key": cfg.specter_api_key})
    if res["status"] != "ok":
        return res
    data = res["data"]
    rows = data if isinstance(data, list) else data.get("results") if isinstance(data, dict) else None
    if rows is None:
        return error("bad_response", "Specter response was neither a list nor {'results': [...]}")
    if not rows:
        return empty(f"Specter has no company matching {domain}")
    return ok(company=rows[0])
