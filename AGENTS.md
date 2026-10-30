# Agent Guide

- Use `python3`; no interactive prompts. Tool functions take `cfg` + args and return a dict with a `status` (see `funnel/tools/base.py`).
- Never hardcode/print/commit secrets (`.env`). Load from env via `funnel/config.py`.
- Surface auth/request/empty/shape failures distinctly; never collapse them into empty results.
- Write tools must honour `cfg.dry_run` and be idempotent in code, not left to the prompt.
- Add a test in `tests/` for each tool or behaviour change; run `python3 -m pytest`.
