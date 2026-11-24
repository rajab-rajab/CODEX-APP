# Changelog

What changed in each version of Codex App. `/update` shows the new entries before it updates.

## 1.1.1 (2026-11-24)

- `py main.py --update` exits with an error when a check couldn't be done or an update didn't work
  (for example GitHub or PyPI out of reach). Being already up to date is not an error.
- The daily update check tries again at the next start when it couldn't reach GitHub or PyPI,
  instead of waiting a day.
- With no Codex engine installed and no version this app accepts, `--version` says so instead of
  "up to date".
- The start message only says "Codex reads" for `AGENTS.md`. Codex doesn't read `CLAUDE.md`.

## 1.1.0 (2026-11-21)

- The Codex engine updates too: `py main.py --update` installs the newest `openai-codex` that this
  app version accepts (the range in requirements.txt), checks it starts, and puts the old one back
  if it doesn't.
- The daily check, `/version` and `--version` cover both the app and the engine. `/update` inside
  the app says when a new engine is waiting.
- requirements.txt: `openai-codex>=0.171,<0.200` and `packaging`.

## 1.0.1 (2026-11-21)

- `webhost-install.txt`: installing Codex App on a Windows PC and on Mountain Web Hosting.

## 1.0.0 (2026-11-21)

- Built on OpenAI's Codex Python SDK (`openai-codex`, from github.com/openai/codex).
- A) Single model mode (gpt-6.2-sol) and B) dual mode (gpt-6.2-astra planner in a read-only
  sandbox, gpt-6.2-sol worker, with review rounds; the planner runs the tests itself).
- Permissions through Codex's sandbox: plan, workspace, full. Requests to leave it: deny_all or auto_review.
- Sign-in with OPENAI_API_KEY or a ChatGPT account (`/login`).
- Saved conversations (`--continue`, `/sessions`, `/resume`), `/init` for AGENTS.md, `/models`, `/usage`.
- Updates from GitHub: `/update` or `py main.py --update`, with a daily check.
