# Changelog

What changed in each version of Codex App. `/update` shows the new entries before it updates.

## Unreleased

Faster to start, and cheaper in dual mode.

- The daily update check runs in the background while Codex starts, with GitHub and PyPI asked at the
  same time. Starting no longer waits for it (before, a start with the check due took about 10 s; now
  about 4 s, the same as any start), and quitting never waits for it either. Its notice shows at the
  first prompt after it's ready. If you quit before it finishes, the next start checks again.
- `--version` and `/version` ask GitHub and PyPI at the same time (about 2 s instead of 4 s).
- Dual mode: the planner gets the worker's exact diff with its report, so it reviews the changes
  instead of re-reading whole files, which saves tokens on every review round.

## 1.2.0 (2026-11-24)

Closer to official Codex, and safer for the system disk.

- New commands, as in official Codex: `/model`, `/status`, `/diff`, `/review` (uncommitted changes, a
  branch, a commit, or your own instructions; read-only), `/compact`, `/new`, `/fork`, `/rename`,
  `/logout`, `/mention`, `/image`, `/approval`. `/permissions`, `/approvals` and `/quit` work as aliases.
- `@path` in a message attaches that file; `--image` and `/image` attach pictures.
- While Codex works: its plan as a checklist, the diff of the files it changed, and after each answer
  the tokens used and how much context is left.
- Options as in official Codex: `--cd`/`-C`, `-m`, `--effort`, `--sandbox`/`-s` (with official names
  `read-only`, `workspace-write`, `danger-full-access`), `--ask-for-approval`/`-a`, `--image`/`-i`,
  `--search`, and `-c key=value`. **`-c` now means `--config`**; use `--continue` to carry on.
- `--approval ask` (experimental): you answer y/n when Codex wants to leave the sandbox.
- System protection: the app refuses a disk root, a system folder or your whole home folder as the
  project; refuses requests that would format disks, change the registry or boot settings, delete
  system folders and the like; and `full` permission needs you to type `yes`.
- Efforts `none` and `minimal` are accepted, as Codex offers them.

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
