"""Webhook intake (fallback to Graph, e.g. a Zap that catches the email and POSTs it here).

POST /inbound/email   header X-Funnel-Secret: <FUNNEL_WEBHOOK_SECRET>
{"from": "Name <a@b.com>", "subject": "...", "body": "...",
 "attachments": [ "https://.../file"                                   # Zapier-style file URL, or
                  {"filename": "deck.pdf", "url": "https://..."},      # URL with a name, or
                  {"filename": "deck.pdf", "content_type": "application/pdf", "content_base64": "..."} ]}
Attachment URLs must be https and must not point at private/internal addresses.
"""
import base64
import hmac
import ipaddress
import json
import logging
import mimetypes
import re
import socket
import threading
from urllib.parse import unquote, urljoin, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from ..models import Attachment, Inbound
from ..textutil import html_to_text

log = logging.getLogger("funnel.webhook")
MAX_BODY = 40_000_000
MAX_ATTACHMENT = 25_000_000


def _check_host(url: str):
    """Raise ValueError unless url is https and resolves only to public addresses (SSRF guard)."""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("attachment URL must be https")
    for info in socket.getaddrinfo(u.hostname, u.port or 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("attachment URL resolves to a non-public address")


def fetch_attachment(url: str, filename: str = "", content_type: str = "") -> Attachment:
    for _ in range(4):  # follow a few redirects manually so each hop is checked
        _check_host(url)
        r = requests.get(url, timeout=60, stream=True, allow_redirects=False)
        if r.is_redirect:
            url = urljoin(url, r.headers.get("location", ""))
            continue
        r.raise_for_status()
        data = r.raw.read(MAX_ATTACHMENT + 1, decode_content=True)
        if len(data) > MAX_ATTACHMENT:
            raise ValueError("attachment too large")
        break
    else:
        raise ValueError("too many redirects")
    if not filename:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', r.headers.get("content-disposition", ""))
        filename = unquote(m.group(1)) if m else unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "attachment"
    ctype = content_type or r.headers.get("content-type", "").split(";")[0] or mimetypes.guess_type(filename)[0] or ""
    return Attachment(filename, ctype, data)


def payload_to_inbound(p: dict) -> Inbound:
    body = p.get("body") or ""
    if "<html" in body.lower() or "<div" in body.lower():
        body = html_to_text(body)
    atts, problems = [], []
    for a in p.get("attachments") or []:
        a = {"url": a} if isinstance(a, str) else a
        name = a.get("filename", "")
        try:
            if a.get("content_base64"):
                atts.append(Attachment(name or "attachment", a.get("content_type", ""),
                                       base64.b64decode(a["content_base64"])))
            elif a.get("url"):
                atts.append(fetch_attachment(a["url"], name, a.get("content_type", "")))
        except (ValueError, requests.RequestException) as exc:
            problems.append(f"{name or a.get('url', 'attachment')}: {exc}")
    if problems:  # never drop silently: tell the agent so it reports it
        body += "\n\n[Attachments that could not be loaded: " + "; ".join(problems) + "]"
    return Inbound(source="email", text=body, sender=p.get("from", ""), subject=p.get("subject", ""),
                   attachments=atts)


def make_handler(cfg, process):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, msg):
            self.send_response(code)
            self.end_headers()
            self.wfile.write(msg.encode())

        def do_POST(self):
            if self.path != "/inbound/email":
                return self._reply(404, "not found")
            if not cfg.webhook_secret or not hmac.compare_digest(
                    self.headers.get("X-Funnel-Secret", ""), cfg.webhook_secret):
                return self._reply(401, "bad secret")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._reply(413, "too large")
            try:
                item = payload_to_inbound(json.loads(self.rfile.read(length)))
            except (ValueError, KeyError) as exc:
                return self._reply(400, f"bad payload: {exc}")
            threading.Thread(target=process, args=(item,), daemon=True).start()
            self._reply(202, "accepted")

        def log_message(self, *a):  # keep secrets/bodies out of access logs
            pass

    return Handler


def serve(cfg, process):
    server = ThreadingHTTPServer(("0.0.0.0", cfg.webhook_port), make_handler(cfg, process))
    log.info("webhook listening on :%d", cfg.webhook_port)
    server.serve_forever()
