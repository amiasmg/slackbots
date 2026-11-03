"""Microsoft Graph intake: poll a shared mailbox for unread forwarded emails.

Azure app registration (client credentials) needs *application* permission Mail.ReadWrite
(read, and mark processed). Restrict it to this one mailbox with an Exchange application
access policy so the app cannot read anyone else's mail.
"""
import base64
import logging
import threading

from ..models import Attachment, Inbound
from ..textutil import html_to_text
from ..tools.base import api_call

log = logging.getLogger("funnel.graph")
GRAPH = "https://graph.microsoft.com/v1.0"
MAX_ATTEMPTS = 3
MAX_ATTACHMENT_BYTES = 25_000_000


def get_token(cfg) -> dict:
    return api_call("Microsoft Graph", "POST",
                    f"https://login.microsoftonline.com/{cfg.ms_tenant_id}/oauth2/v2.0/token",
                    data={"client_id": cfg.ms_client_id, "client_secret": cfg.ms_client_secret,
                          "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"})


def _sender(msg: dict) -> str:
    addr = (msg.get("from") or {}).get("emailAddress") or {}
    return f"{addr.get('name', '')} <{addr.get('address', '')}>".strip()


def to_inbound(cfg, headers: dict, msg: dict) -> tuple:
    """Return (Inbound, None) or (None, error_dict). Attachments are fetched eagerly."""
    body = msg.get("body") or {}
    text = html_to_text(body["content"]) if body.get("contentType") == "html" else body.get("content", "")
    atts = []
    if msg.get("hasAttachments"):
        res = api_call("Microsoft Graph", "GET", f"{GRAPH}/users/{cfg.mailbox}/messages/{msg['id']}/attachments",
                       headers=headers)
        if res["status"] != "ok":
            return None, res
        for a in (res["data"] or {}).get("value", []):
            if a.get("@odata.type") != "#microsoft.graph.fileAttachment" or a.get("isInline"):
                continue  # skip signature images and item attachments
            if (a.get("size") or 0) > MAX_ATTACHMENT_BYTES or not a.get("contentBytes"):
                log.warning("skipping attachment %s (too large or no content)", a.get("name"))
                continue
            atts.append(Attachment(a.get("name", "attachment"), a.get("contentType", ""),
                                   base64.b64decode(a["contentBytes"])))
    return Inbound(source="email", text=text, sender=_sender(msg), subject=msg.get("subject", ""),
                   attachments=atts), None


def poll_once(cfg, process, failures: dict) -> int:
    """Process unread mail. `process(inbound) -> bool`. Returns how many were handled."""
    token = get_token(cfg)
    if token["status"] != "ok":
        log.error("Graph auth failed: %s", token)
        return 0
    headers = {"Authorization": f"Bearer {token['data']['access_token']}",
               "Prefer": 'outlook.body-content-type="text"'}
    inbox = api_call("Microsoft Graph", "GET", f"{GRAPH}/users/{cfg.mailbox}/mailFolders/inbox/messages",
                     headers=headers, params={"$filter": "isRead eq false", "$top": 20,
                                              "$select": "id,subject,from,body,hasAttachments"})
    if inbox["status"] != "ok":
        log.error("Graph inbox read failed: %s", inbox)
        return 0
    handled = 0
    for msg in (inbox["data"] or {}).get("value", []):
        item, err = to_inbound(cfg, headers, msg)
        ok_ = bool(item) and process(item)
        if not ok_:
            failures[msg["id"]] = failures.get(msg["id"], 0) + 1
            log.error("failed processing %r (attempt %d): %s", msg.get("subject"), failures[msg["id"]], err)
            if failures[msg["id"]] < MAX_ATTEMPTS:
                continue  # leave unread, retry next poll
        # success, or gave up: mark read so a poison email can't loop forever
        api_call("Microsoft Graph", "PATCH", f"{GRAPH}/users/{cfg.mailbox}/messages/{msg['id']}",
                 headers=headers, json={"isRead": True})
        failures.pop(msg["id"], None)
        handled += 1
    return handled


def run_forever(cfg, process, stop: threading.Event):
    failures = {}
    while not stop.is_set():
        try:
            poll_once(cfg, process, failures)
        except Exception:
            log.exception("mail poll crashed; will retry")
        stop.wait(cfg.mail_poll_seconds)
