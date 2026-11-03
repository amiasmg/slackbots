"""Local test without Slack:
python3 -m funnel.cli "check out https://acme.com" [--pdf deck.pdf] [--email-from "Pat <p@qed.com>"] [--dry-run]"""
import argparse
import dataclasses
import json
import os

import anthropic

from .agent import run_agent
from .config import load_config
from .models import Attachment, Inbound
from .tools.urls import extract_company_urls


def main():
    p = argparse.ArgumentParser()
    p.add_argument("message")
    p.add_argument("--pdf", action="append", default=[], help="attach a PDF (repeatable)")
    p.add_argument("--email-from", help="treat as a forwarded email from this sender")
    p.add_argument("--dry-run", action="store_true", help="never write to Affinity")
    args = p.parse_args()
    cfg = load_config()
    if args.dry_run:
        cfg = dataclasses.replace(cfg, dry_run=True)
    atts = [Attachment(os.path.basename(f), "application/pdf", open(f, "rb").read()) for f in args.pdf]
    item = Inbound("email" if args.email_from else "slack", args.message, args.email_from or "",
                   "(cli test)" if args.email_from else "", atts)
    result = run_agent(anthropic.Anthropic(), cfg, item, extract_company_urls(args.message))
    print(json.dumps(result.tool_calls, indent=2))
    print("\n" + result.text)


if __name__ == "__main__":
    main()
