# slackbots

Top-of-funnel pipeline agent. Replaces three Zaps (*Top of Funnel => Deal Pipeline*, *Pitchbook summary*, *Summarize Previous Interactions*) with one Slack bot that uses Claude with tool-calling.

Someone posts a company URL in `#top-of-funnel-pipeline`; the bot replies in-thread with: pipeline status (adds to Affinity if missing), a Pitchbook/Specter snapshot, and a summary of prior notes/meetings/emails. `@mention` it in a thread for follow-ups.

## Design

Code does what must be deterministic; the model decides what to do and writes the summary.

| Old zap step | Now |
|---|---|
| Filters (bot name, "text exists", "URL exists") | Loop guard on own messages in `app.py`; model replies `NO_REPLY` to chatter |
| GPT "fix doubled URL" + Formatter "Extract URL" | `tools/urls.py` (tested) |
| Pitchbook code steps (search, bio, investors) | `tools/pitchbook.py`; partial failures reported, not swallowed |
| Affinity find org / "ping Affinity API" x3 | `tools/affinity.py` (find, pipeline status, notes, interactions) |
| Add to pipeline if absent (Path A/B: already in list vs. add) | `affinity_add_to_pipeline` re-checks membership in code, so one call covers both paths and is idempotent |
| GPT extracts company name, stage, founder emails; Python parses emails | The model reads the message directly; `affinity_get_list_fields` gives it the valid Stage options |
| Affinity "Get Person ID based on Slack" | `app.py` resolves the poster's Slack email; agent calls `affinity_find_person` |
| Loop over emails: find person | `affinity_link_person` (finds, attaches to company; creates only if a name is given) |
| "Update stage and Owners" | `affinity_set_list_entry_field` (Stage replaces, Owners appends; never wipes existing owners) |
| "Create a Note" (Slack post; Pitchbook summary) | `affinity_create_note`, duplicate-safe via a content-hash marker |
| Google Sheet to batch emails for LLM, "Ask Gemini" | Removed: interactions are trimmed/capped in code and summarised in-context |
| Paths (has history / no history) | Model branches itself ("first touch") |

Every tool returns a `status` of `ok`, `empty`, `auth_error`, `request_error`, `bad_response`, `not_configured` or `dry_run`, so a broken key is never reported as "company not found".

Extending: write a function in `funnel/tools/`, add one `Tool(...)` entry in `funnel/tools/__init__.py`, adjust `prompts.py` if behaviour should change.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in keys
python3 -m pytest
```

Slack app: Socket Mode on; bot scopes `channels:history`, `groups:history` (private channel), `chat:write`, `app_mentions:read`, `users:read`, `users:read.email` (to map the poster to an Affinity owner); subscribe to `message.channels`/`message.groups` and `app_mention`; invite the bot to the channel. Pause the old zaps before starting the bot or both will reply.

Try it without Slack (reads only):

```bash
python3 -m funnel.cli "check out https://example.com" --dry-run
python3 -m funnel.app   # run the bot
```

## Unverified assumptions (check with live keys)

- Affinity: v1 REST with Basic auth (blank user); interactions return metadata only (date, subject, people), not email bodies.
- Specter: endpoint/auth in `tools/specter.py` follow the public enrichment API but were not tested.
- Pitchbook: search still uses `www.<domain>` as the old zap did.
- Pipeline columns are matched by name (`Stage`, `Owners`); dropdown values are written as option ids and person columns as person ids per Affinity v1 field-values. Confirm against your list.
- Contacts are attached to the company, which is my reading of the old 'find person by email' loop; tell me if it did something else.
- Bot only reads top-level channel messages; replies in threads need an `@mention`.
