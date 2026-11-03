"""Pitchbook lookup. The API bills per field/endpoint, so we only fetch what the summary needs:
investors and deals (-> total raised, last round date). Bio is opt-in; descriptions come from the web.

ASSUMPTION: GET /companies/{id}/deals exists alongside /investors; verify with your key.
"""
from .base import api_call, cached, empty, error, ok

BASE = "https://api-v2.pitchbook.com"
MAX_INVESTORS = 25
MAX_DEALS = 15
SECTIONS = ("investors", "deals", "bio")


def _headers(cfg) -> dict:
    return {"accept": "*/*", "Authorization": f"PB-Token {cfg.pitchbook_api_key}"}


def _lookup(cfg, domain: str, sections: tuple) -> dict:
    headers = _headers(cfg)
    # The old zap searched on "www.<domain>"; keep that behaviour.
    search = api_call("Pitchbook", "GET", f"{BASE}/companies/search",
                      params={"companyNames": f"www.{domain}"}, headers=headers)
    if search["status"] != "ok":
        return search
    items = (search["data"] or {}).get("items") if isinstance(search["data"], dict) else None
    if items is None:
        return error("bad_response", "Pitchbook search response had no 'items' field")
    if not items:
        return empty(f"Pitchbook has no company matching {domain}")
    match = items[0]
    pb_id = match.get("companyId")
    if not pb_id:
        return error("bad_response", "Pitchbook match had no companyId")

    result = {"company_id": pb_id, "match": match, "errors": []}
    for section in sections:
        res = api_call("Pitchbook", "GET", f"{BASE}/companies/{pb_id}/{section}", headers=headers)
        if res["status"] != "ok":
            result["errors"].append({"section": section, **res})
            continue
        data, cap = res["data"], {"investors": MAX_INVESTORS, "deals": MAX_DEALS}.get(section)
        if cap and isinstance(data, list) and len(data) > cap:
            result[f"{section}_truncated_from"] = len(data)
            data = data[:cap]
        result[section] = data
    return ok(**result)


def pitchbook_lookup(cfg, domain: str, sections: list = None) -> dict:
    if not cfg.pitchbook_api_key:
        return error("not_configured", "PITCHBOOK_API_KEY is not set")
    wanted = tuple(s for s in (sections or ["investors", "deals"]) if s in SECTIONS)
    if not wanted:
        return error("request_error", f"sections must be from {SECTIONS}")
    return cached(cfg, f"pb:{domain}:{','.join(sorted(wanted))}", lambda: _lookup(cfg, domain, wanted))
