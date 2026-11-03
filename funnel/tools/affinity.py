"""Affinity v1 REST tools. Auth: HTTP Basic, blank username, API key as password.

ASSUMPTION: v1 endpoints (organizations, lists/{id}/list-entries, notes, interactions).
Interactions return metadata (date, subject, participants), not email bodies.
"""
import hashlib
import io
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


def affinity_get_interactions(cfg, organization_id: int, kind: str, since_days: int = 730,
                              person_id: int = 0) -> dict:
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
            "type": INTERACTION_TYPES[kind],
            **({"person_id": person_id} if person_id else {"organization_id": organization_id}),
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


# ---- people, notes and pipeline fields (stage / owners) -------------------------------

def affinity_find_person(cfg, email: str) -> dict:
    if (e := _check(cfg)):
        return e
    res = api_call("Affinity", "GET", f"{BASE}/persons", params={"term": email}, auth=_auth(cfg))
    if res["status"] != "ok":
        return res
    people = (res["data"] or {}).get("persons") if isinstance(res["data"], dict) else None
    if people is None:
        return error("bad_response", "Affinity /persons had no 'persons' field")
    exact = [p for p in people if email.lower() in [x.lower() for x in (p.get("emails") or [])]]
    if not exact:
        return empty(f"No Affinity person with email {email}")
    p = exact[0]
    return ok(id=p["id"], name=f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
              organization_ids=p.get("organization_ids") or [])


def affinity_link_person(cfg, email: str, organization_id: int,
                         first_name: str = "", last_name: str = "") -> dict:
    """Ensure a contact is attached to the company. Creates the person only if both names are given."""
    found = affinity_find_person(cfg, email)
    if found["status"] == "ok":
        if organization_id in found["organization_ids"]:
            return ok(linked=False, already_linked=True, person_id=found["id"])
        if cfg.dry_run:
            return {"status": "dry_run", "message": f"Would link person {found['id']} to org {organization_id}"}
        res = api_call("Affinity", "PUT", f"{BASE}/persons/{found['id']}", auth=_auth(cfg),
                       json={"organization_ids": found["organization_ids"] + [organization_id]})
        return ok(linked=True, person_id=found["id"]) if res["status"] == "ok" else res
    if found["status"] != "empty":
        return found
    if not (first_name and last_name):
        return empty(f"{email} is not in Affinity; give first_name and last_name to create them")
    if cfg.dry_run:
        return {"status": "dry_run", "message": f"Would create {first_name} {last_name} <{email}>"}
    res = api_call("Affinity", "POST", f"{BASE}/persons", auth=_auth(cfg), json={
        "first_name": first_name, "last_name": last_name, "emails": [email],
        "organization_ids": [organization_id]})
    return ok(linked=True, created=True, person_id=res["data"].get("id")) if res["status"] == "ok" else res


def affinity_create_note(cfg, organization_id: int, content: str, kind: str) -> dict:
    """Idempotent via a content-hash marker, so Slack redeliveries don't duplicate notes."""
    if (e := _check(cfg)):
        return e
    marker = f"[funnel:{kind}:{hashlib.sha1(content.encode()).hexdigest()[:10]}]"
    existing = affinity_get_notes(cfg, organization_id)
    if existing["status"] not in ("ok", "empty"):
        return existing
    if existing["status"] == "ok" and any(marker in (n["content"] or "") for n in existing["notes"]):
        return ok(created=False, duplicate=True)
    if cfg.dry_run:
        return {"status": "dry_run", "message": f"Would create '{kind}' note on org {organization_id}"}
    res = api_call("Affinity", "POST", f"{BASE}/notes", auth=_auth(cfg),
                   json={"organization_ids": [organization_id], "content": f"{content}\n\n{marker}"})
    return ok(created=True, note_id=res["data"].get("id")) if res["status"] == "ok" else res


def _list_fields(cfg):
    res = api_call("Affinity", "GET", f"{BASE}/lists/{cfg.affinity_list_id}", auth=_auth(cfg))
    if res["status"] != "ok":
        return None, res
    fields = (res["data"] or {}).get("fields")
    if fields is None:
        return None, error("bad_response", "Affinity list response had no 'fields'")
    return fields, None


def affinity_get_list_fields(cfg) -> dict:
    """Pipeline columns (e.g. Stage, Owners) with dropdown options, so the agent can pick valid values."""
    if (e := _check(cfg)):
        return e
    fields, err = _list_fields(cfg)
    if err:
        return err
    return ok(fields=[{"name": f.get("name"), "value_type": f.get("value_type"),
                       "allows_multiple": f.get("allows_multiple"),
                       "options": [o.get("text") for o in f.get("dropdown_options") or []]}
                      for f in fields])


def _plain(v):
    return v.get("id") if isinstance(v, dict) else v


def affinity_set_list_entry_field(cfg, list_entry_id: int, organization_id: int,
                                  field_name: str, value, mode: str = "replace") -> dict:
    """Set a pipeline column. Dropdowns take the option text; person columns take a person id.
    mode 'replace' overwrites a single value (Stage); 'add' appends without removing (Owners)."""
    if (e := _check(cfg)):
        return e
    fields, err = _list_fields(cfg)
    if err:
        return err
    field = next((f for f in fields if (f.get("name") or "").lower() == field_name.lower()), None)
    if not field:
        return error("request_error", f"No pipeline column '{field_name}'. Columns: "
                     f"{[f.get('name') for f in fields]}")
    opts = field.get("dropdown_options") or []
    if opts:
        match = next((o for o in opts if (o.get("text") or "").lower() == str(value).lower()), None)
        if not match:
            return error("request_error", f"'{value}' is not an option for {field_name}: "
                         f"{[o.get('text') for o in opts]}")
        value = match["id"]
    cur = api_call("Affinity", "GET", f"{BASE}/field-values",
                   params={"list_entry_id": list_entry_id}, auth=_auth(cfg))
    if cur["status"] != "ok":
        return cur
    same = [v for v in cur["data"] if v.get("field_id") == field["id"]] if isinstance(cur["data"], list) else []
    if any(_plain(v.get("value")) == value for v in same):
        return ok(changed=False, already_set=True)
    if cfg.dry_run:
        return {"status": "dry_run", "message": f"Would set {field_name}={value} on entry {list_entry_id}"}
    if same and mode == "replace":
        res = api_call("Affinity", "PUT", f"{BASE}/field-values/{same[0]['id']}",
                       json={"value": value}, auth=_auth(cfg))
    else:
        res = api_call("Affinity", "POST", f"{BASE}/field-values", auth=_auth(cfg), json={
            "field_id": field["id"], "entity_id": organization_id,
            "list_entry_id": list_entry_id, "value": value})
    return ok(changed=True) if res["status"] == "ok" else res


# ---- CRM depth: people, files, uploads -----------------------------------------------

MAX_PEOPLE = 25
MAX_FILE_CHARS = 15000


def affinity_company_people(cfg, organization_id: int) -> dict:
    """People Affinity has at the company (to spot the CEO/founders), with their emails."""
    if (e := _check(cfg)):
        return e
    org = api_call("Affinity", "GET", f"{BASE}/organizations/{organization_id}", auth=_auth(cfg))
    if org["status"] != "ok":
        return org
    ids = (org["data"] or {}).get("person_ids") or []
    if not ids:
        return empty("Affinity has no people attached to this company")
    people, errors = [], []
    for pid in ids[:MAX_PEOPLE]:
        res = api_call("Affinity", "GET", f"{BASE}/persons/{pid}", auth=_auth(cfg))
        if res["status"] != "ok":
            errors.append(res)
            continue
        p = res["data"]
        people.append({"id": p["id"], "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                       "emails": p.get("emails") or []})
    return ok(people=people, truncated=len(ids) > MAX_PEOPLE, errors=errors)


def affinity_find_internal_user(cfg, name: str) -> dict:
    """Resolve a teammate's name (e.g. a partner mentioned in the message) to an Affinity person id."""
    if (e := _check(cfg)):
        return e
    res = api_call("Affinity", "GET", f"{BASE}/persons", params={"term": name}, auth=_auth(cfg))
    if res["status"] != "ok":
        return res
    people = (res["data"] or {}).get("persons") if isinstance(res["data"], dict) else None
    if people is None:
        return error("bad_response", "Affinity /persons had no 'persons' field")
    internal = [p for p in people if p.get("type") == 1]  # 1 = internal (your firm's users)
    if not internal:
        return empty(f"No internal Affinity user matching '{name}'")
    return ok(matches=[{"id": p["id"], "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                        "emails": p.get("emails") or []} for p in internal[:5]],
              ambiguous=len(internal) > 1)


def affinity_list_files(cfg, organization_id: int) -> dict:
    if (e := _check(cfg)):
        return e
    res = api_call("Affinity", "GET", f"{BASE}/entity-files", auth=_auth(cfg),
                   params={"organization_id": organization_id, "page_size": MAX_ITEMS})
    if res["status"] != "ok":
        return res
    files = (res["data"] or {}).get("entity_files") if isinstance(res["data"], dict) else None
    if files is None:
        return error("bad_response", "Affinity /entity-files had no 'entity_files' field")
    if not files:
        return empty("No files on this company")
    files.sort(key=lambda f: f.get("created_at") or "", reverse=True)
    return ok(files=[{"id": f["id"], "name": f.get("name"), "size": f.get("size"),
                      "created_at": f.get("created_at")} for f in files])


def _extract_text(content: bytes, content_type: str, name: str) -> dict:
    lower = (name or "").lower()
    if lower.endswith(".pdf") or "pdf" in content_type:
        try:
            from pypdf import PdfReader

            pages = PdfReader(io.BytesIO(content)).pages
            text = "\n".join((p.extract_text() or "") for p in pages[:40])
        except Exception as exc:
            return error("bad_response", f"Could not read PDF: {exc}")
        if not text.strip():
            return empty("PDF has no extractable text (likely scanned or image-only)")
        return ok(text=text[:MAX_FILE_CHARS], truncated=len(text) > MAX_FILE_CHARS, pages=len(pages))
    if lower.endswith((".txt", ".md", ".csv")) or content_type.startswith("text/"):
        text = content.decode("utf-8", errors="replace")
        return ok(text=text[:MAX_FILE_CHARS], truncated=len(text) > MAX_FILE_CHARS)
    return error("unsupported", f"Cannot read {name or 'this file'} ({content_type or 'unknown type'}); only PDF/text")


def affinity_read_file(cfg, file_id: int, name: str = "") -> dict:
    """Download a CRM file and return its text (PDF/text only). Use for decks, memos, model summaries."""
    if (e := _check(cfg)):
        return e
    res = api_call("Affinity", "GET", f"{BASE}/entity-files/download/{file_id}", auth=_auth(cfg), raw=True)
    if res["status"] != "ok":
        return res
    return _extract_text(res["content"], res["content_type"], name)


def affinity_attach_inbound_file(cfg, ctx, organization_id: int, attachment_index: int) -> dict:
    """File an attachment from the inbound message/email on the company. Skips if already there."""
    if (e := _check(cfg)):
        return e
    atts = getattr(ctx, "attachments", None) or []
    if not 0 <= attachment_index < len(atts):
        return error("request_error", f"attachment_index must be 0..{len(atts) - 1}" if atts
                     else "This message has no attachments")
    att = atts[attachment_index]
    existing = affinity_list_files(cfg, organization_id)
    if existing["status"] not in ("ok", "empty"):
        return existing
    if existing["status"] == "ok" and any(f["name"] == att.filename and f.get("size") == len(att.data)
                                          for f in existing["files"]):
        return ok(uploaded=False, already_filed=True, filename=att.filename)
    if cfg.dry_run:
        return {"status": "dry_run", "message": f"Would file {att.filename} on org {organization_id}"}
    res = api_call("Affinity", "POST", f"{BASE}/entity-files", auth=_auth(cfg),
                   data={"organization_id": organization_id},
                   files={"file": (att.filename, att.data, att.content_type)})
    return ok(uploaded=True, filename=att.filename) if res["status"] == "ok" else res
