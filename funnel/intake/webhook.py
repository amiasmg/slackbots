"""Webhook intake (fallback to Graph, e.g. a Zap that catches the email and POSTs it here).

POST /inbound/email   header X-Funnel-Secret: <FUNNEL_WEBHOOK_SECRET>
{"from": "Name <a@b.com>", "subject": "...", "body": "...",
 "attachments": [{"filename": "deck.pdf", "content_type": "application/pdf", "content_base64": "..."}]}
"""
import base64
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..models import Attachment, Inbound
from ..textutil import html_to_text

log = logging.getLogger("funnel.webhook")
MAX_BODY = 40_000_000


def payload_to_inbound(p: dict) -> Inbound:
    body = p.get("body") or ""
    if "<html" in body.lower() or "<div" in body.lower():
        body = html_to_text(body)
    atts = [Attachment(a.get("filename", "attachment"), a.get("content_type", ""),
                       base64.b64decode(a["content_base64"]))
            for a in p.get("attachments") or [] if a.get("content_base64")]
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
