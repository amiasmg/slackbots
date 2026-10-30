"""Pitchbook lookup: search by domain, then bio + investors. Partial results are reported, not hidden."""
from .base import api_call, empty, error, ok

BASE = "https://api-v2.pitchbook.com"
MAX_INVESTORS = 25


def _headers(cfg) -> dict:
    return {"accept": "*/*", "Authorization": f"PB-Token {cfg.pitchbook_api_key}"}


def pitchbook_lookup(cfg, domain: str) -> dict:
    if not cfg.pitchbook_api_key:
        return error("not_configured", "PITCHBOOK_API_KEY is not set")
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

    result = {"company_id": pb_id, "match": match, "bio": None, "investors": None, "errors": []}
    bio = api_call("Pitchbook", "GET", f"{BASE}/companies/{pb_id}/bio", headers=headers)
    if bio["status"] == "ok":
        result["bio"] = bio["data"]
    else:
        result["errors"].append({"section": "bio", **bio})
    inv = api_call("Pitchbook", "GET", f"{BASE}/companies/{pb_id}/investors", headers=headers)
    if inv["status"] == "ok":
        data = inv["data"]
        if isinstance(data, list) and len(data) > MAX_INVESTORS:
            result["investors_truncated_from"] = len(data)
            data = data[:MAX_INVESTORS]
        result["investors"] = data
    else:
        result["errors"].append({"section": "investors", **inv})
    return ok(**result)
