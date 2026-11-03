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
- **Idempotent writes in code**: pipeline add, notes (content-hash marker), file upload, owners (append-only), stage (only set on newly added companies, enforced by the prompt, see assumptions). Slack redeliveries and email retries do not duplicate anything.
- **Honest errors**: every tool returns `status` of `ok`, `empty`, `auth_error`, `request_error`, `bad_response`, `unsupported`, `not_configured` or `dry_run`. A broken key is never reported as "not found".
- **Cost control**: Pitchbook fetches only investors + deals (bio is opt-in); Specter and Pitchbook results are cached per domain (`FUNNEL_CACHE_TTL_HOURS`).
- **Dry run**: `FUNNEL_DRY_RUN=true` reads everything and writes nothing.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in keys
python3 -m pytest
```

**Slack app**: Socket Mode on; bot scopes `channels:history` (`groups:history` if private), `chat:write`, `app_mentions:read`, `users:read`, `users:read.email`, `files:read`; subscribe to `message.channels`/`message.groups` and `app_mention`; invite the bot to the channel. Pause the old zaps first or both will reply.

**Email via Microsoft Graph (preferred)**: register an Azure app, grant *application* permission `Mail.ReadWrite`, then have IT restrict it to the `qed.topfunnel@` mailbox with an Exchange application access policy (otherwise the app can read every mailbox). Set `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`. The bot polls the inbox, processes unread mail, then marks it read. Failures are retried 3 times, then an error is posted in the channel and the email is marked read.

**Email via webhook (fallback)**: set `FUNNEL_WEBHOOK_PORT` and `FUNNEL_WEBHOOK_SECRET`; have a Zap (or anything) POST to `/inbound/email` with header `X-Funnel-Secret` and JSON `{from, subject, body, attachments:[{filename, content_type, content_base64}]}`. Put it behind HTTPS.

Try it without Slack:

```bash
python3 -m funnel.cli "check out https://example.com, cc Sam" --dry-run
python3 -m funnel.cli "fwd: Acme intro" --email-from "Pat <pat@qedinvestors.com>" --pdf deck.pdf --dry-run
python3 -m funnel.app   # run the bot
```

## Unverified assumptions (check with live keys)

- **Affinity** v1 REST, Basic auth with blank user. Interactions return metadata, not email bodies. Internal teammates are `type == 1` in `/persons`. File listing/download/upload use `/entity-files`. Pipeline columns are matched by name (`Stage`, `Owners`) and must contain a `Watchlist` option; otherwise the agent reports the error instead of guessing.
- **Pitchbook**: `/companies/{id}/deals` is assumed to exist next to `/investors`; the response field names are not hard-coded, the model reads them.
- **Specter**: the endpoint in `tools/specter.py` follows the public enrichment API but is untested. It returns the whole record per call, so it is cached; once you see the real response and pricing, narrow it.
- **Stage rule**: "set Watchlist only if newly added" is enforced by the prompt, not code.
- **Web search** uses Anthropic's server-side `web_search` tool (`FUNNEL_WEB_SEARCH_TOOL` if the tool version changes).
- Emailed results post in the Slack channel naming the forwarder (not by email reply).
- Scanned or image-only PDFs are read visually by Claude, but the Affinity file reader (`affinity_read_file`) needs extractable text.
- Slack thread replies are only handled via `@mention`.
