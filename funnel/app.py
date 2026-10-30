"""Slack entrypoint (Socket Mode). Run: python3 -m funnel.app"""
import logging
import re

import anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .agent import run_agent
from .config import load_config
from .tools.urls import extract_company_urls

log = logging.getLogger("funnel")
SKIP_SUBTYPES = {"message_changed", "message_deleted", "channel_join", "channel_leave"}


def build_app(cfg, client=None):
    app = App(token=cfg.slack_bot_token)
    llm = client or anthropic.Anthropic()
    me = app.client.auth_test()
    my_user, my_bot = me["user_id"], me.get("bot_id")
    seen = set()  # Slack can redeliver events

    def handle(text, channel, thread_ts, context=""):
        try:
            result = run_agent(llm, cfg, text, extract_company_urls(text), context)
            log.info("tools=%s", result.tool_calls)
            if not result.silent:
                app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=result.text)
        except Exception as exc:
            log.exception("agent failed")
            app.client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f"⚠️ Pipeline agent failed: {type(exc).__name__}: {exc}")

    @app.event("message")
    def on_message(event):
        if event.get("channel") != cfg.channel or event.get("subtype") in SKIP_SUBTYPES:
            return
        if event.get("user") == my_user or (my_bot and event.get("bot_id") == my_bot):
            return  # never react to ourselves
        ts = event["ts"]
        if event.get("thread_ts") not in (None, ts) or (event["channel"], ts) in seen:
            return  # thread replies are handled via @mention
        seen.add((event["channel"], ts))
        handle(event.get("text", ""), event["channel"], ts)

    @app.event("app_mention")
    def on_mention(event):
        thread = event.get("thread_ts") or event["ts"]
        replies = app.client.conversations_replies(channel=event["channel"], ts=thread, limit=20)
        context = "\n".join(m.get("text", "") for m in replies.get("messages", []) if m["ts"] != event["ts"])
        text = re.sub(rf"<@{my_user}>", "", event.get("text", "")).strip()
        handle(text, event["channel"], thread, context)

    return app


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    SocketModeHandler(build_app(cfg), cfg.slack_app_token).start()


if __name__ == "__main__":
    main()
