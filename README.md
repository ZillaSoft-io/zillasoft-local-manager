# ZillaSoft Local Manager

Multi-agent orchestration for bug fixes, feature implementation, and new-app
creation across the three ZillaSoft projects (Zillasoft website, Snipzilla,
Stashzilla).

- **Haiku 4.5** — orchestrator & input handler (clarifying questions, context fetch, dry-run validation)
- **Sonnet 4.6** — requirement parser & reviewer (dry-run plan, instructions, test/review)
- **Opus 4.8** — code fixer & builder (implements, commits locally)

Mario approves before any deployment. That is the only approval gate.

## Status

Built phase by phase per `LOCAL_MANAGER_ARCHITECTURE.md`.

| Phase | Scope | State |
|---|---|---|
| 1 | Core infrastructure (FastAPI server, SQLite schema, ConfigHandler, auth) | ✅ done |
| 2 | Agent integration (+ 2b dry-run validation) | ✅ done |
| 3 | Input handling (chatbox, Sentry/Jira, vision, scope) | ✅ done |
| 4 | Cost tracking, kill switch, pause/resume, notifications | ✅ done |
| 5 | Pre-flight checks & code execution (orchestrator loop) | ✅ done |
| 6 | Git operations (approve/push, reject, rollback) | ✅ done |
| 7 | Auto-configuration (new apps) | ✅ done |
| 8 | Deployment tracking (GHA/Railway/AWS/health) | ✅ done |
| 9 | UI / web frontend (buildless single-page) | ✅ done |
| 10 | Testing & polish | ⏳ |

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then fill in the <FILL> credential values
python run.py            # serves on http://localhost:5555
```

On first launch a `LOCAL_MANAGER_AUTH_TOKEN` is auto-generated and written to
`.env`; it gates the API. The token is printed to the console on startup.

## Configuration

All configuration lives in `.env` (gitignored). `.env.example` documents the
structure. Write access is tiered (`app/config.py`):

- **Credentials** (`ANTHROPIC_API_KEY`, `*_API_TOKEN`, `*_AUTH_TOKEN`, `AWS_*`,
  `STRIPE_*`, `AUTH0_*`, `BREVO_API_KEY`, `GITHUB_TOKEN`): Mario-only, via the UI.
  Agent writes raise `CredentialWriteError`.
- **Project / model / manager / notification settings**: agent-writable.
