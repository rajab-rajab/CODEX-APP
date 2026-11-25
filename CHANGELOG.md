# Changelog

What changed in each version of Codex App. `/update` shows the new entries before it updates.

## Unreleased

- **Team mode: each worker in its own git worktree**, the way official Codex keeps parallel work apart.
  Workers no longer see each other's half-written files or test runs. Each worker's copy starts from the
  project as it is now, uncommitted changes and new files included (ignored files such as `.env` are
  not copied). When the workers are done, their changes are merged back into the project folder with
  git's three-way merge, so parts that edit different lines of one file both land. A part that conflicts
  is left out, and the planner is told to have it redone. Your branches, index, stash and commits are
  never touched, and the worktrees are removed afterwards. This needs git 2.40 or newer and a git
  project; otherwise, or with `CODEX_APP_TEAM_WORKTREES=no`, workers share the project folder as before.

## 1.9.1 (2026-11-25)

- **Misuse protection for the browser**, whatever the user says or approves:
  - attack strings (SQL, script, command, template and Log4Shell-style injection, path traversal; also
    URL-encoded) are refused in typed text and addresses, while ordinary text and code still work;
  - private, link-local and cloud-metadata addresses are blocked for every request a page makes,
    redirects included (localhost stays allowed; `CODEX_APP_BROWSER_PRIVATE=yes` allows them);
  - rate limits stop brute force and floods; pages with a CAPTCHA are never clicked or typed into;
  - `CODEX_APP_BROWSER_SITES` limits the browser to listed sites;
  - every action and refusal goes into a misuse log (never what was typed); `/browser log` shows it;
  - Codex is told never to attack, scan or test the security of any site.
- Fixed: a public page could redirect the browser to a private address, because Playwright doesn't route
  the target of a redirect. The browser now checks where every redirect points before following it.

## 1.9.0 (2026-11-25)

- **Built-in browser** (`--browser`, `/browser on`): Codex can visit websites and act on them when you
  ask. It is Codex App's own private browser (your installed Edge or Chrome with a fresh, empty profile,
  so none of your sign-ins), in a window you can watch. Clicks and submits that buy, send, post, delete or
  the like wait for your y/N; it never types passwords or card numbers; web pages reach Codex as
  untrusted data; only http and https open and downloads are blocked. The rules are in the browser's own
  code. Needs `py -m pip install playwright`.

## 1.8.1 (2026-11-25)

- **Fixed (safety)**: computer use, browsers and account plugins are now off in Codex App by default.
  Codex App shares the Codex engine's settings with the official Codex apps, so plugins they installed
  (computer use, browser, Chrome, Slack, Google Calendar ...) also reached Codex App's conversations.
  Those tools work outside the sandbox, beyond `workspace` permission, the secret guard and the
  system-folder protection, and the computer-use tool is marked read-only, so approvals don't stop it.
  The app now starts the engine with them switched off for its own conversations (your Codex settings
  aren't changed) and lists what it switched off; `--outside-tools` turns them on. The desktop app's
  computer-use hook no longer runs after Codex App's turns.

## 1.8.0 (2026-11-25)

New engines are tested before you get them, and every update says what's new.

- **Weekly engine check on GitHub**: every Monday the newest Codex engine is installed and all tests
  and the offline self-test run with it on Windows and Linux. If it passes and the app doesn't accept
  it yet, a pull request widens the accepted range; if it fails, an issue says what broke.
- **What's new**: `/update`, `--version` and the daily notice show the CHANGELOG entries of every
  version between yours and the new one, instead of commit titles.
- **GitHub Releases**: raising the version publishes a release with its CHANGELOG notes.
- **Faster tests** (about 2 minutes instead of 3 on Windows) and a coverage report on GitHub.

## 1.7.0 (2026-11-24)

Checked on every change, scriptable, and real costs.

- **Tests on GitHub**: every pull request and push to `main` runs all tests on Windows and Linux, plus
  the offline part of `--selftest` (`--selftest --offline`). No key is needed and nothing is billed.
- **`--json`**: one task, and only its result on stdout as JSON (answer, files changed, tokens, cost,
  success), for scripts, scheduled jobs and CI. The exit code says whether it succeeded.
- **`/export [file]`**: the conversation as Markdown, with commands, their output and diffs.
- **`/prices`**: enter your models' prices once; `/usage` and the line after each task show dollars.
- **`/stats [days|all]`**: tokens and cost per day and per project, from a usage log that never holds
  your messages.

## 1.6.0 (2026-11-24)

Safe engine updates, Codex's own review, and help for long sessions.

- **`--selftest`**: checks in a few seconds, without a model turn, that the Codex engine still has
  everything Codex App uses. `--update` now runs it after installing a new engine and puts the old one
  back if anything fails (before, it only checked that the engine started).
- **`/review`** uses Codex's own code review (`review/start`), as official Codex does, and falls back
  to the app's review instructions if it isn't available. Review and commit-message conversations are
  kept out of Codex's history.
- **`/history`**: Codex's conversations for this project from every Codex client, including the official
  Codex CLI, with who made each; `/history <number>` carries on with one.
- **Auto-compact** when only 10% of the context is left, and **`/queue <task>`** to line up the next task
  while Codex works.
- Fixed: Ctrl+C now really stops Codex; before, the app stopped waiting while Codex carried on in the
  background, and its work could mix with the next message.
- Fixed: the secret guard no longer stops commands that leave secret files out (such as
  `Where-Object { $_ -ne '.env' }`), and a turn that can't be wound back after a guard stop now gets
  a fresh conversation, so what Codex read is never sent again.

## 1.5.0 (2026-11-24)

More reliable, and the work goes straight to git.

- **Automatic retries**: a turn that fails because OpenAI is busy or had an internal error is tried
  again after 5, 15 and 45 seconds, carrying on where it stopped (`CODEX_APP_RETRIES`).
- **Limit warnings**: with a ChatGPT sign-in, `/status` shows plan limits and their reset times, the app
  warns once a limit is 80% used, and says when one is hit.
- **`/commit`**: Codex writes the commit message from the diff; you approve or edit it. Secret files
  are never committed.
- **`/pr`**: pushes the work to a branch and opens a pull request with GitHub's `gh`, after asking;
  commits made on `main` move to a new branch first.
- **`/save`**: remembers this project's mode, power, profile, permission and models for the next start.
- **Finish signal**: a beep and a Windows notification when a task took 30 seconds or more.

## 1.4.0 (2026-11-24)

Safer and more capable: undo, a secret guard, and Codex's tools and skills.

- **`/undo`**, as in official Codex: puts the files and the conversation back to before your last
  message (again to go further back), after showing what it will change. In a git project it uses
  private snapshots that never touch your index, branches or history, and never changes files your
  `.gitignore` leaves out.
- **Secret guard**: when Codex starts to read or change a secret file (`.env`, keys, credentials,
  databases, `data/`, `.ssh/` ...), the turn is stopped and taken out of the conversation, so what Codex
  read is never sent to OpenAI again. Requests to leave the sandbox that touch secret files are refused.
  The app lists the secret files in a project at start; `/secrets` lists them and turns the guard on or off.
- **`/mcp`** lists Codex's MCP servers (external tools) with their status and tools, and **`/skills`**
  lists its skills; `$name` in a message attaches a skill.
- Fixed: on Windows, `/diff` and the background update check could hang since 1.3.0, because git
  inherited the input the steering thread was reading. Every command the app starts now gets its own.

## 1.3.0 (2026-11-24)

The most powerful Codex App yet. Every new feature was tested with real tasks on Codex engine 0.172.0.

- **Goal mode** (`/goal`, `--goal`, as in official Codex): Codex keeps working on its own, over as many
  turns as it needs, until the goal is done. `--budget 500k` stops it after about that many tokens.
- **Team mode** (`--mode team`): the planner splits a task into parts that touch different files, and
  up to 3 workers (`--team-size`) do them at the same time; the planner then checks everything.
- **Steering**: type while Codex works to add instructions to what it's doing; `/stop` stops it.
- **Power presets** (`/power`, `--power`): `high` and `max` switch every role to the most capable model
  at higher effort; `normal` goes back.
- **Task profiles** (`/profile`, `--profile`, default `auto`): chat is answered directly at low effort
  (and in dual/team mode without the worker), maths gets at least high effort and checks its answer,
  coding works as before.
- **Structured verdicts**: in dual and team mode the planner's verdict comes as structured output, so it
  can't be missed or misread. It falls back to text if an engine or model can't do that.

## 1.2.2 (2026-11-24)

`--approval ask` tested with real tasks on Codex engine 0.172.0, and fixed.

- Codex now asks to change files outside the project folder instead of refusing outright. The 1.2.0
  rule "never touch anything outside the project folder" stopped it from asking at all. System folders,
  the registry, disks and the like are still never allowed.
- The approval prompt shows the command itself (`curl -sI https://example.com`), not the PowerShell
  wrapper Codex puts around every command on Windows. The system-folder check also looks at the
  command itself, so the wrapper's path under `C:\Program Files` can never cause a wrong refusal.
- A refused command shows "did not run" instead of "exit code None".

## 1.2.1 (2026-11-24)

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
