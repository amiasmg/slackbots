"""Shared result shapes and HTTP helper.

Every tool returns a dict with a `status` so the agent (and logs) can tell apart:
ok, empty (valid empty result), auth_error, request_error, bad_response,
not_configured, dry_run.
"""
import requests

TIMEOUT = 30


def ok(**data) -> dict:
    return {"status": "ok", **data}


def empty(message: str) -> dict:
    return {"status": "empty", "message": message}


def error(kind: str, message: str) -> dict:
    return {"status": kind, "message": message}


def api_call(service: str, method: str, url: str, **kwargs) -> dict:
    """Return ok(data=<json>) or an error dict; never raises."""
    try:
        resp = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        return error("request_error", f"{service}: {exc}")
    if resp.status_code in (401, 403):
        return error("auth_error", f"{service} rejected credentials (HTTP {resp.status_code})")
    if resp.status_code == 429:
        return error("request_error", f"{service} rate limited (HTTP 429)")
    if not resp.ok:
        return error("request_error", f"{service} HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return ok(data=resp.json())
    except ValueError:
        return error("bad_response", f"{service} returned non-JSON: {resp.text[:200]}")
