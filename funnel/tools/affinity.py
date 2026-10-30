"""Affinity v1 REST tools. Auth: HTTP Basic, blank username, API key as password.

ASSUMPTION: v1 endpoints (organizations, lists/{id}/list-entries, notes, interactions).
Interactions return metadata (date, subject, participants), not email bodies.
"""
from datetime import datetime, timedelta, timezone

from .base import api_call, empty, error, ok

BASE = "https://api.affinity.co"
INTERACTION_TYPES = {"meetings": 0, "calls": 1, "emails": 3}
MAX_ITEMS = 100


def _auth(cfg):
    return ("", cfg.affinity_api_key)


def _check(cfg):
    if not cfg.affinity_api_key:
        return error("not_configured", "AFFINITY_API_KEY is not set")
    return None


def affinity_find_company(cfg, domain: str) -> dict:
    if (e := _check(cfg)):
        return e
    res = api_call("Affinity", "GET", f"{BASE}/organizations",
                   params={"term": domain}, auth=_auth(cfg))
    if res["status"] != "ok":
        return res
    orgs = (res["data"] or {}).get("organizations") if isinstance(res["data"], dict) else None
    if orgs is None:
        return error("bad_response", "Affinity /organizations had no 'organizations' field")
    exact = [o for o in orgs if domain in ([o.get("domain")] + (o.get("domains") or []))]
    if not exact:
        near = [{"id": o["id"], "name": o.get("name"), "domain": o.get("domain")} for o in orgs[:5]]
        return {"status": "empty", "message": f"No Affinity company with domain {domain}",
                "near_matches": near}
    return ok(companies=[{"id": o["id"], "name": o.get("name"), "domain": o.get("domain")}
                         for o in exact])


def _pipeline_entry(cfg, organization_id: int):
    """Return (entry_or_None, error_dict_or_None)."""
    res = api_call("Affinity", "GET", f"{BASE}/organizations/{organization_id}", auth=_auth(cfg))
    if res["status"] != "ok":
        return None, res
    entries = (res["data"] or {}).get("list_entries") or []
    for entry in entries:
        if entry.get("list_id") == cfg.affinity_list_id:
            return entry, None
    return None, None


def affinity_pipeline_status(cfg, organization_id: int) -> dict:
    if (e := _check(cfg)):
        return e
    entry, err = _pipeline_entry(cfg, organization_id)
    if err:
        return err
    return ok(in_pipeline=entry is not None, list_id=cfg.affinity_list_id,
              list_entry_id=entry.get("id") if entry else None)


def affinity_create_company(cfg, name: str, domain: str) -> dict:
    if (e := _check(cfg)):
        return e
    if cfg.dry_run:
        return {"status": "dry_run", "message": f"Would create Affinity company {name} ({domain})"}
    res = api_call("Affinity", "POST", f"{BASE}/organizations",
                   json={"name": name, "domain": domain}, auth=_auth(cfg))
    if res["status"] != "ok":
        return res
    return ok(id=res["data"].get("id"), name=res["data"].get("name"))


def affinity_add_to_pipeline(cfg, organization_id: int) -> dict:
    """Idempotent: re-checks membership in code so the model cannot create duplicates."""
    if (e := _check(cfg)):
        return e
    if not cfg.affinity_list_id:
        return error("not_configured", "AFFINITY_LIST_ID is not set")
    entry, err = _pipeline_entry(cfg, organization_id)
    if err:
        return err
    if entry:
        return ok(added=False, already_in_pipeline=True, list_entry_id=entry.get("id"))
    if cfg.dry_run:
        return {"status": "dry_run", "message": f"Would add organization {organization_id} to list {cfg.affinity_list_id}"}
    res = api_call("Affinity", "POST", f"{BASE}/lists/{cfg.affinity_list_id}/list-entries",
                   json={"entity_id": organization_id}, auth=_auth(cfg))
    if res["status"] != "ok":
        return res
    return ok(added=True, list_entry_id=res["data"].get("id"))


def affinity_get_notes(cfg, organization_id: int) -> dict:
    if (e := _check(cfg)):
        return e
    res = api_call("Affinity", "GET", f"{BASE}/notes",
                   params={"organization_id": organization_id, "page_size": MAX_ITEMS},
                   auth=_auth(cfg))
    if res["status"] != "ok":
        return res
    notes = (res["data"] or {}).get("notes") if isinstance(res["data"], dict) else None
    if notes is None:
        return error("bad_response", "Affinity /notes had no 'notes' field")
    if not notes:
        return empty("No notes on this company")
    notes.sort(key=lambda n: n.get("created_at") or "", reverse=True)
    return ok(count=len(notes), notes=[
        {"created_at": n.get("created_at"), "creator_id": n.get("creator_id"),
         "content": (n.get("content") or "")[:2000]} for n in notes])


def affinity_get_interactions(cfg, organization_id: int, kind: str, since_days: int = 730) -> dict:
    """kind: meetings | calls | emails. Newest first, capped at 100, windowed to <=1 year per call."""
    if (e := _check(cfg)):
        return e
    if kind not in INTERACTION_TYPES:
        return error("request_error", f"kind must be one of {sorted(INTERACTION_TYPES)}")
    end = datetime.now(timezone.utc)
    floor = end - timedelta(days=since_days)
    items = []
    while end > floor and len(items) < MAX_ITEMS:
        start = max(end - timedelta(days=365), floor)
        res = api_call("Affinity", "GET", f"{BASE}/interactions", auth=_auth(cfg), params={
            "type": INTERACTION_TYPES[kind], "organization_id": organization_id,
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "page_size": MAX_ITEMS})
        if res["status"] != "ok":
            return res
        key = {"meetings": "events", "calls": "events", "emails": "emails"}[kind]
        data = res["data"] if isinstance(res["data"], dict) else {}
        items.extend(data.get(key) or data.get("events") or [])
        end = start
    if not items:
        return empty(f"No {kind} in the last {since_days} days")
    items.sort(key=lambda i: i.get("date") or "", reverse=True)
    total = len(items)
    return ok(count=total, truncated=total > MAX_ITEMS, interactions=[
        {"date": i.get("date"), "subject": i.get("subject"), "direction": i.get("direction"),
         "persons": [p.get("first_name", "") + " " + p.get("last_name", "")
                     for p in (i.get("persons") or [])][:8]}
        for i in items[:MAX_ITEMS]])
