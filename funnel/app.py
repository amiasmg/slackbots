"""Entrypoint: Slack (Socket Mode) + optional Graph mailbox poller + optional webhook.
Run: python3 -m funnel.app"""
import logging
import re
import threading

import anthropic
import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .agent import run_agent
from .config import load_config
from .intake import graphmail, webhook
from .models import Attachment, Inbound
from .tools.urls import extract_company_urls

log = logging.getLogger("funnel")
SKIP_SUBTYPES = {"message_changed", "message_deleted", "channel_join", "channel_leave"}
MAX_SLACK_FILE = 25_000_000


def build_app(cfg, client=None):
    app = App(token=cfg.slack_bot_token)
    llm = client or anthropic.Anthropic()
    me = app.client.auth_test()
    my_user, my_bot = me["user_id"], me.get("bot_id")
    seen = set()  # Slack can redeliver events

    def person(user_id):
        """'Name <email>' for a Slack user (needs users:read + users:read.email); degrades gracefully."""
        try:
            u = app.client.users_info(user=user_id)["user"]
            return f"{u.get('real_name') or u.get('name')} <{u.get('profile', {}).get('email', 'no email')}>"
        except Exception as exc:
            log.warning("could not resolve Slack user %s: %s", user_id, exc)
            return ""

    def poster_of(user_id):
        return person(user_id) if user_id else "(bot/integration)"

    def resolve_mentions(text):
        """Turn <@U123> into 'Name <email>' so 'cc <@U123>' can become an Affinity owner."""
        return re.sub(r"<@(\w+)>", lambda m: person(m.group(1)) or m.group(0), text)

    def slack_attachments(event):
        out = []
        for f in event.get("files") or []:
            if f.get("size", 0) > MAX_SLACK_FILE or not f.get("url_private_download"):
                continue
            try:
                r = requests.get(f["url_private_download"], timeout=60,
                                 headers={"Authorization": f"Bearer {cfg.slack_bot_token}"})
                r.raise_for_status()
                out.append(Attachment(f.get("name", "file"), f.get("mimetype", ""), r.content))
            except requests.RequestException as exc:  # needs files:read scope
                log.warning("could not download Slack file %s: %s", f.get("name"), exc)
        return out

    def process(item: Inbound, thread_ts=None) -> bool:
        """Run the agent and post the result. Returns False on failure (after posting an error)."""
        try:
            hints = extract_company_urls(item.text)
            result = run_agent(llm, cfg, item, hints)
            log.info("%s tools=%s", item.source, result.tool_calls)
            if result.silent:
                return True
            text = result.text
            if item.source == "email":
                text = f"📧 Forwarded by {item.sender or 'unknown'}: *{item.subject or '(no subject)'}*\n{text}"
            app.client.chat_postMessage(channel=cfg.channel, thread_ts=thread_ts, text=text)
            return True
        except Exception as exc:
            log.exception("agent failed")
            app.client.chat_postMessage(
                channel=cfg.channel, thread_ts=thread_ts,
                text=f"⚠️ Pipeline agent failed on '{item.subject or item.text[:60]}': {type(exc).__name__}: {exc}")
            return False

    our_threads = set()  # threads we have replied in (rebuilt lazily after a restart)

    def is_ours(m):
        return m.get("user") == my_user or (my_bot and m.get("bot_id") == my_bot)

    def thread_messages(channel, thread):
        return app.client.conversations_replies(channel=channel, ts=thread, limit=30).get("messages", [])

    def follow_up(event, thread):
        """Reply in a thread: answer new instructions/questions, stay silent on chatter (NO_REPLY)."""
        msgs = thread_messages(event["channel"], thread)
        context = "\n".join(m.get("text", "") for m in msgs if m["ts"] != event["ts"])
        text = resolve_mentions(re.sub(rf"<@{my_user}>", "", event.get("text", "")).strip())
        item = Inbound("slack", text, poster_of(event.get("user")), attachments=slack_attachments(event))
        try:
            result = run_agent(llm, cfg, item, extract_company_urls(text + "\n" + context), context or "(thread)")
            log.info("followup tools=%s", result.tool_calls)
            if not result.silent:
                app.client.chat_postMessage(channel=event["channel"], thread_ts=thread, text=result.text)
        except Exception as exc:
            log.exception("agent failed")
            app.client.chat_postMessage(channel=event["channel"], thread_ts=thread,
                                        text=f"⚠️ Pipeline agent failed: {type(exc).__name__}: {exc}")

    @app.event("message")
    def on_message(event):
        if event.get("channel") != cfg.channel or event.get("subtype") in SKIP_SUBTYPES:
            return
        if is_ours(event):
            return  # never react to ourselves
        ts = event["ts"]
        if (event["channel"], ts) in seen:
            return
        thread = event.get("thread_ts")
        if thread not in (None, ts):  # a reply inside a thread
            if f"<@{my_user}>" in event.get("text", ""):
                return  # on_mention handles explicit mentions
            if thread not in our_threads:
                if not any(is_ours(m) for m in thread_messages(event["channel"], thread)):
                    return  # not a conversation we took part in: leave humans alone
                our_threads.add(thread)
            seen.add((event["channel"], ts))
            return follow_up(event, thread)
        seen.add((event["channel"], ts))
        if process(Inbound("slack", resolve_mentions(event.get("text", "")), poster_of(event.get("user")),
                           attachments=slack_attachments(event)), thread_ts=ts):
            our_threads.add(ts)

    @app.event("app_mention")
    def on_mention(event):
        if (event["channel"], event["ts"]) in seen:
            return
        seen.add((event["channel"], event["ts"]))
        follow_up(event, event.get("thread_ts") or event["ts"])

    app.funnel_process = process  # for the email adapters
    return app


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    app = build_app(cfg)
    stop = threading.Event()
    if cfg.graph_enabled:
        threading.Thread(target=graphmail.run_forever, args=(cfg, app.funnel_process, stop), daemon=True).start()
        log.info("polling %s every %ds", cfg.mailbox, cfg.mail_poll_seconds)
    else:
        log.info("Graph not configured (MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET): email via webhook only")
    if cfg.webhook_port:
        threading.Thread(target=webhook.serve, args=(cfg, app.funnel_process), daemon=True).start()
    SocketModeHandler(app, cfg.slack_app_token).start()


if __name__ == "__main__":
    main()
