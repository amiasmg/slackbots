"""Local test without Slack: python3 -m funnel.cli "check out https://acme.com" [--dry-run]"""
import argparse
import dataclasses
import json

import anthropic

from .agent import run_agent
from .config import load_config
from .tools.urls import extract_company_urls


def main():
    p = argparse.ArgumentParser()
    p.add_argument("message")
    p.add_argument("--dry-run", action="store_true", help="never write to Affinity")
    args = p.parse_args()
    cfg = load_config()
    if args.dry_run:
        cfg = dataclasses.replace(cfg, dry_run=True)
    result = run_agent(anthropic.Anthropic(), cfg, args.message, extract_company_urls(args.message))
    print(json.dumps(result.tool_calls, indent=2))
    print("\n" + result.text)


if __name__ == "__main__":
    main()
