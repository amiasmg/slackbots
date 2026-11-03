# slackbots

Top-of-funnel pipeline agent. When a team member posts a company URL in `#top-of-funnel-pipeline`, or forwards an email (often with a deck) to `qed.topfunnel@qedinvestors.com`, the bot runs three tasks and posts one summary. It replaces the old Zaps (*Top of Funnel => Deal Pipeline*, *Pitchbook summary*, *Summarize Previous Interactions*).

## What it does

1. **Quick research**: Pitchbook (leading investors, total raised, last round), Specter (momentum, hiring), and open-web search (what they do, customers, team bios).
2. **CRM history** (Affinity): do we know the CEO, when did we last speak, notes/files from those meetings, and what they projected then.
3. **Pipeline**: add to the deal pipeline if missing with Stage = `Watchlist`, add any teammate mentioned in the message as Owner, save the original message and the research as Affinity notes, and file attachments on the company.

PDFs (Slack uploads or email attachments) are read by Claude directly, which is also how the company URL is found when the message has none.

## Design

Code does what must be deterministic or safe; the model decides what to do and writes the summary.

- **Intake is pluggable.** Slack, a Graph mailbox poller and a webhook all produce one `Inbound` object (`funnel/models.py`) for the same agent.
- **Tools** live in `funnel/tools/`; add one function and one `Tool(...)` entry in `funnel/tools/__init__.py`.
- **Rules enforced in code, not the prompt**: Stage is set to `Watchlist` only inside `affinity_add_to_pipeline` when it creates a new entry; the agent-facing setter refuses Stage entirely. Pipeline add, notes (content-hash marker), file upload and owners (append-only) are idempotent, so Slack redeliveries and email retries do not duplicate anything.
- **Honest errors**: every tool returns `status` of `ok`, `empty`, `auth_error`, `request_error`, `bad_response`, `unsupported`, `not_configured` or `dry_run`. A broken key is never reported as "not found".
- **Cost control**: Pitchbook fetches only investors + deals (bio is opt-in); Specter and Pitchbook results are cached per domain (`FUNNEL_CACHE_TTL_HOURS`).
- **Dry run**: `FUNNEL_DRY_RUN=true` reads everything and writes nothing.

## Setup

### 1. Keys go in `.env`

```bash
cp .env.example .env     # .env is gitignored; never commit it or paste keys into chat/issues
```

Then fill in `.env` (a plain `KEY=value` file in the repo root, loaded automatically):

| Variable | Where to get it |
|---|---|
| `SLACK_BOT_TOKEN` (`xoxb-`) | Slack app > OAuth & Permissions (after installing to the workspace) |
| `SLACK_APP_TOKEN` (`xapp-`) | Slack app > Basic Information > App-Level Tokens, scope `connections:write` |
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `AFFINITY_API_KEY` | Affinity > Settings > API |
| `AFFINITY_LIST_ID` | the number in your deal pipeline list's URL (`.../lists/<id>`) |
| `PITCHBOOK_API_KEY`, `SPECTER_API_KEY` | your vendor accounts (rotate the old Pitchbook key that was in the Zap) |
| `FUNNEL_WEBHOOK_SECRET` | make one: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |

On a host (Railway/Fly/Render/etc.) put the same names in the host's environment-variable settings instead of a file.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 -m pytest
```

### 2. Slack app

Socket Mode on; bot scopes `channels:history` (`groups:history` if private), `chat:write`, `app_mentions:read`, `users:read`, `users:read.email`, `files:read`; subscribe to `message.channels`/`message.groups` and `app_mention`; invite the bot to the channel. Pause the old zaps first or both will reply.

### 3. Email intake via webhook (default)

The bot exposes `POST /inbound/email` when `FUNNEL_WEBHOOK_PORT` is set (the Dockerfile sets 8080). It needs a **public HTTPS URL**, so deploy it somewhere that gives you one (the Dockerfile works as-is on Railway/Fly/Render; put TLS in front).

A Zap feeds it: trigger *Microsoft Outlook > New Email* on `qed.topfunnel@`, action *Webhooks by Zapier > Custom Request*: method `POST`, URL `https://<your-host>/inbound/email`, header `X-Funnel-Secret: <FUNNEL_WEBHOOK_SECRET>`, data (JSON)

```json
{"from": "<From Name> <<From Email>>", "subject": "<Subject>", "body": "<Body Plain>", "attachments": ["<Attachment URL>"]}
```

Attachments can be plain file URLs (what Zapier provides), `{filename, url}`, or `{filename, content_type, content_base64}`. URLs must be https and public. A file that cannot be downloaded is reported in the Slack summary instead of being dropped. The Zap must be able to read that mailbox (connect it as, or with delegated access to, `qed.topfunnel@`).

*Alternative, needs IT*: Microsoft Graph polling (`MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`) with an Azure app that has application permission `Mail.ReadWrite`, restricted to that one mailbox by an Exchange application access policy (otherwise it can read every mailbox). If Graph is configured, both run.

Try it without Slack:

```bash
python3 -m funnel.cli "check out https://example.com, cc Sam" --dry-run
python3 -m funnel.cli "fwd: Acme intro" --email-from "Pat <pat@qedinvestors.com>" --pdf deck.pdf --dry-run
python3 -m funnel.app   # run the bot
```

## Threads

Replies inside a thread the bot has answered are handled without an `@mention` ("add Sam as owner", "actually the site is x.com"); the agent stays silent on chatter. Threads the bot never replied in are ignored.

## Unverified assumptions (check with live keys)

- **Affinity** v1 REST, Basic auth with blank user. Interactions return metadata, not email bodies. Internal teammates are `type == 1` in `/persons`. Files use `/entity-files`. Pipeline columns are matched by name (`Stage`, `Owners`); Stage needs a `Watchlist` option (a missing option surfaces as `stage_error`, the entry still gets created).
- **Pitchbook**: `/companies/{id}/deals` is assumed to exist next to `/investors`; response field names are not hard-coded, the model reads them.
- **Specter**: the endpoint in `tools/specter.py` follows the public enrichment API but is untested. It returns the whole record per call, so it is cached; once you see the real response and pricing, narrow it.
- **Web search** uses Anthropic's server-side tool (`FUNNEL_WEB_SEARCH_TOOL` if the tool version changes).
- Scanned PDFs are read visually by Claude (email attachments directly; Affinity files via a follow-up attachment in the same turn). Total PDF payload per run is capped at 20 MB.
- Emailed results post in the Slack channel naming the forwarder.
