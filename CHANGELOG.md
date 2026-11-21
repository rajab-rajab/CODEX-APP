# Changelog

What changed in each version of Codex App. `/update` shows the new entries before it updates.

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
