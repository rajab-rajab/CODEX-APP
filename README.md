# Codex App

OpenAI's **Codex** ([github.com/openai/codex](https://github.com/openai/codex), Apache 2.0) in your
terminal, with three ways of working. It uses OpenAI's official Codex Python SDK (`openai-codex`),
which installs the real Codex engine. So the reading, editing, command running and sandboxing are
all done by Codex itself. This app adds the three modes, goals, task profiles, power presets, saved
conversations and self-updates.

## Three modes

| Mode | Who does what | Models (default) |
|---|---|---|
| **A) Single model** | One Codex thread reads the code, makes the changes and checks them | `gpt-6.2-sol` |
| **B) Dual model** | A **planner** reads the code and writes a plan. A **worker** carries it out. The planner then reviews the diff and the worker's test results, and either approves or sends the work back with fixes (up to 2 times). A small change whose tests passed skips the review | Planner `gpt-6.2-astra`, worker `gpt-6.2-sol` |
| **C) Team** (`--mode team`) | Like dual, but the planner splits the task into parts that touch different files, and up to 3 **workers** (`--team-size`) do them **at the same time**. The planner then checks everything together | Planner `gpt-6.2-astra`, workers `gpt-6.2-sol` |

In dual and team mode:

- The planner always works in Codex's **read-only sandbox**. It can read files and run tests, but
  it can't change anything.
- A question that needs no change is answered by the planner alone, and the worker isn't called.
- **The review only runs when it's worth it** (`CODEX_APP_PLANNER_REVIEW=auto`, the default). It is
  skipped when the worker's own tests passed (the last test command it ran exited with 0) and the change
  is small (`CODEX_APP_REVIEW_MAX_LINES`, 200 changed lines). It always runs when the tests failed or
  weren't run, the change is bigger, the browser check would look at it, or in team mode when several
  workers' parts were merged (they were never tested together), a part failed or a part wasn't merged.
  The app says when it skips one. `always` reviews every change; `never` turns the review off, except in
  those team cases. In the benchmark the review hardly ever changed the result, but cost about a sixth of
  dual mode's tokens on the dearer model.
- **The planner gets the worker's test results**: the test commands Codex recorded, their exit codes and
  the end of their output. It runs tests itself only when there are none, they failed, or they don't
  cover the change (and in team mode on the merged result), instead of running them all again.
- **The review runs at a lower effort than the plan**: one level below the planner's (`high` -> `medium`),
  never below `medium` (`CODEX_APP_REVIEW_EFFORT` sets it). It checks a diff the worker already tested.
- The planner's verdict comes as **structured output** (JSON that Codex must follow), so it can't be
  missed or misread. If an engine or model can't do that, the app falls back to a text verdict.
- Parts that would change the same file never run at the same time in team mode.
- In team mode, each worker gets **its own git worktree**, a temporary copy of the project, as in
  official Codex. Workers then never see each other's half-written files or test runs. The copy starts
  from the project as it is now, uncommitted changes and new files included; files that `.gitignore`
  leaves out (`.env`, installed dependencies, build output) are not copied, so the planner runs the
  tests on the merged result. When the workers are done, their changes are merged into your project
  folder with git's three-way merge, one worker after another, so edits to different lines of one file
  both land. A part that conflicts is left out and the planner has it redone. Your branches, index,
  stash and commits are never touched, and the worktrees are deleted afterwards. If you stop the task
  (Ctrl+C or `/stop`) while workers are busy, their unmerged work is thrown away. This needs a git
  project and git 2.40 or newer; otherwise, with one worker, or with `CODEX_APP_TEAM_WORKTREES=no`,
  workers share the project folder as before.

## Built-in browser

Codex App has its own private web browser, so Codex can visit websites and act on them when you ask:
"open our site and check the contact form works", "find the opening hours on example.com", "fill in this
form with the details I gave you". Turn it on with `--browser`, or `/browser on` in the app (a new
conversation starts); `/save` remembers it for the project.

- **Private.** It is Microsoft Edge (or Chrome) with a fresh, empty profile each time: none of your
  sign-ins, cookies, history or extensions, and nothing is kept. It is not the computer-use or browser
  plugins of the official Codex apps, which drive your own signed-in browser and stay off (see
  "Protecting your system").
- **You can watch.** A browser window opens and shows what Codex does. `CODEX_APP_BROWSER_HEADLESS=yes`
  runs it without a window (for example on a server); `CODEX_APP_BROWSER_CHANNEL=chrome` uses Chrome.
- **Risky actions wait for you.** Before a click or submit that could do something real (buy, pay, order,
  send, post, delete, sign up, book, subscribe, accept ...), Codex App asks you `y/N`. A search, a link
  or typing into an ordinary field doesn't need a yes.
- **No passwords or card numbers.** It never types into password, payment-card or similar fields: if a
  site needs you to sign in or pay, do that yourself in the window.
- **Web pages can't give orders.** What a page says reaches Codex marked as untrusted data, and Codex is
  told never to follow it. Only `http` and `https` addresses open; downloads are blocked.

It needs Playwright, which drives the browser: `py -m pip install playwright`. It uses the Edge or Chrome
already on your PC; if neither is there, `py -m playwright install chromium` downloads one.

### Browser check: web changes looked at before the verdict

In dual and team mode, when the worker changes web files, the changed pages are opened in a real browser
before the planner gives its verdict, like the official Codex app's in-app browser verification. That
covers HTML, CSS, JSX/TSX, Vue and Svelte, and also JavaScript or TypeScript in a project with HTML pages.

- A separate, read-only checker opens the pages in this browser. It runs headless (no window), only on
  this computer (localhost), and with no clicks that would need a yes: there is no one to ask, so the
  browser refuses them.
- The checker reports whether each page loads, whether the change shows up and works, and what is broken.
  The planner gets that as a `BROWSER CHECK` next to the diff and weighs it like a test result. A page that
  doesn't show the change is a reason to send the work back.
- The pages come from a small web server the app runs for the check: the project folder on `127.0.0.1`
  with a random port, read-only, never secret files, `.git` or other hidden folders, and no folder
  listings. It stops when the check ends.
- For an app that needs its own dev server (React, Vue, Next ...), start it yourself and run
  `/browser check http://localhost:3000` (or set `CODEX_APP_CHECK_URL`). `/browser check folder` goes
  back to serving the folder.
- It doesn't need `/browser on`, but it does need Playwright, as above. Without Playwright the review goes
  ahead without it, and the app says so once. Each check is an extra, short model turn, so
  `/browser check off` (or `CODEX_APP_BROWSER_CHECK=no`) turns it off. `/save` keeps the choice for the
  project.

### Misuse protection: no hacking with the browser

The browser must not become a tool for attacking websites. The people misusing a tool are its own users,
so these rules apply whatever the user says or approves (`codex_app/browser_guard.py`):

| Rule | What it stops |
|---|---|
| **No attack input.** Text typed into a page, and addresses opened, are refused when they carry typical attack strings: SQL injection (`' OR '1'='1`, `UNION SELECT`), script injection (`<script>`, `onerror=`), path traversal (`../../etc/passwd`), command injection, template and Log4Shell-style strings, also URL-encoded | Probing or exploiting a site's forms and addresses. Ordinary text and code (a comment with \`code\`, JavaScript with `${price}`) still work |
| **No internal networks.** Private, link-local and cloud-metadata addresses (routers, intranets, `169.254.169.254`) are blocked for every request a page makes, redirects included. Your own computer (`localhost`) stays allowed | Using the browser to reach devices and services behind a firewall |
| **Rate limits.** At most 40 actions a minute, 20 pages a minute per site and 15 submits an hour | Brute-forcing logins, flooding a site, mass sign-ups or posts |
| **No CAPTCHAs.** A page with a CAPTCHA is never clicked or typed into | Getting around a site's bot protection |
| **No passwords or card numbers** (see above) | Credential stuffing, card testing |
| **Optional site allowlist.** `CODEX_APP_BROWSER_SITES=mysite.com,example.org` lets the browser open only those sites (and their subdomains) | Everything off-list, for a shared or company install |
| **Misuse log.** Every action, and every refusal, is recorded in `.codex-app\browser-log.jsonl` (time, site, action, why it was refused; never the text typed). `/browser log` shows the latest | Misuse going unnoticed |
| **Codex is told** never to attack, scan, overload or test the security of any site, or get around its protections, whoever asks and whatever the reason | Misuse requests, before the browser is even used |

What these rules can't do: anyone who runs Codex App on their own computer can change its code, so they
can't stop someone who rewrites the app. They stop misuse *through* Codex App. For a shared install, keep
the app's folder read-only for its users, set `CODEX_APP_BROWSER_SITES`, and review the log. Using any tool
to attack a website is illegal in most countries.

The safety rules are in the browser's own code (`codex_app/browser_mcp.py`), not only in what Codex is
told, so they hold even if a web page tricks Codex.

## Goals, steering, power and task profiles

**Goals** (`/goal`, as in official Codex): `/goal make all the tests pass` lets Codex keep working on
its own, over as many turns as it needs, until the goal is done or blocked. `/goal --budget 500k ...`
stops it after about that many tokens. One-off: `py main.py --goal --budget 500k "make all the tests pass"`.
In dual and team mode the worker works on the goal and the planner checks the result.

**Steering:** while Codex works, type a message and press Enter. It is added to what Codex is doing
right now ("use tabs, not spaces", "also update the README"). `/stop` stops it. Approval questions
(`--approval ask`) are answered the same way.

**Power** (`/power` or `--power`):

| Level | Models and effort |
|---|---|
| `normal` | The settings you started with |
| `high` | `gpt-6.2-astra` for every role, effort `high` (the planner `xhigh`) |
| `max` | `gpt-6.2-astra` for every role, effort `max` (team and dual workers `xhigh`) |

Higher levels give better answers on hard problems and cost more tokens.

**Task profiles** (`/profile` or `--profile`, default `auto`) make each kind of message as strong and as
cheap as it can be. `auto` picks one for each message from its wording, without an extra model call:

| Profile | What happens |
|---|---|
| `coding` | The normal agent work: reads, edits, runs tests |
| `chat` | A question or conversation: answered directly at `low` effort, without the planner/worker steps. Fast and cheap |
| `maths` | A calculation or proof: at least `high` effort, worked step by step, checked (for example with a short Python calculation), ending with `Answer:` |

The default models are the ones Codex recommends in its own list. `gpt-6.2-sol` is its default
("fast, go-to model for coding"). `gpt-6.2-astra` is "our most capable model for complex coding".
Type `/models` to see the list.

## Install

On Windows, start Python with `py` (the Python launcher). If `python` opens another copy, for example one
bundled with another program, `py` still finds the right one.

```bash
git clone https://github.com/rajab-rajab/CODEX-APP.git
```

```bash
cd CODEX-APP
```

```bash
py -m pip install -r requirements.txt
```

The first install downloads the Codex engine, so it takes a few minutes.

## Sign in

Codex App uses Codex's own sign-in. Two ways:

- **OpenAI API key** (you pay per token). Set it once, then open a new terminal:

  ```bash
  setx OPENAI_API_KEY "your-key-here"
  ```

  On first start, Codex App signs Codex in with this key. Codex keeps it in its own sign-in file
  (`~/.codex`), never in a prompt or in this app's files.
- **ChatGPT account** (uses your ChatGPT plan): type `/login`, open the link it shows, and enter the code.

## Run

```bash
py main.py --project "C:\path\to\project"
```

```bash
py main.py --project "C:\path\to\project" --mode dual
```

```bash
py main.py --project "C:\path\to\project" --continue
```

```bash
py main.py --project "C:\path\to\project" --image screenshot.png "why does the page look like this?"
```

`codex-app.cmd` starts it too. Add the folder to your PATH to type `codex-app --mode dual` from anywhere.

The command-line options follow official Codex where they can:

| Option | What it does |
|---|---|
| `--project`, `--cd`, `-C` | The project folder Codex works in |
| `--mode single\|dual\|team` | A) single model, B) planner + worker, C) planner + workers side by side |
| `--goal`, `--budget N` | Treat the task as a goal; stop after about N tokens (for example `500k`) |
| `--power normal\|high\|max` | Stronger models and more reasoning for every role |
| `--profile auto\|coding\|chat\|maths` | How messages are handled (default `auto`) |
| `--team-size N` | Team mode: how many workers at once (default 3, at most 8) |
| `--model`, `-m`, `--effort` | Single mode model and reasoning effort |
| `--planner-model`, `--worker-model` | Dual mode models |
| `--permission`, `--sandbox`, `-s` | `plan`, `workspace`, `full`, or official Codex's `read-only`, `workspace-write`, `danger-full-access` |
| `--approval`, `--ask-for-approval`, `-a` | `deny_all`, `auto_review` or `ask` (see below) |
| `--image`, `-i` | Attach an image (more than once for several) |
| `--search` | Let Codex search the web live |
| `--browser` | Give Codex App's private browser to Codex (see "Built-in browser") |
| `--outside-tools` | Let Codex use plugins that work outside the project: computer use, browsers, account plugins (off by default; see "Protecting your system") |
| `--config`, `-c` | A Codex setting, `-c key=value`, exactly as in official Codex |
| `--json` | With a task: print the result as JSON for scripts (see "Automation") |
| `--selftest [--offline]` | Check the Codex engine still has everything this app uses |
| `--continue` | Carry on with the last conversation |

`-c` now means `--config`, as in official Codex. In 1.1.1 and earlier it was short for `--continue`.

## Commands

| Command | What it does |
|---|---|
Most of these are the same as official Codex's.

| Command | What it does |
|---|---|
| `/mode single`, `/mode dual`, `/mode team` | Switch mode (starts a new conversation) |
| `/goal [--budget N] <goal>` | Codex works on its own until the goal is done (see above) |
| `/power normal\|high\|max` | Stronger models and more reasoning for every role |
| `/profile auto\|coding\|chat\|maths` | How messages are handled |
| `/stop` | While Codex works: stop it (typing anything else steers it) |
| `/queue <task>` | Line up the next task while Codex works; it starts when the current one ends. `/queue` lists them, `/queue clear` empties the queue |
| `/model [name [effort]]` | Show or switch the model and reasoning effort, from the next message on. Dual and team mode: `/model planner <name>` or `/model worker <name>` |
| `/permission <mode>` (or `/permissions`) | What Codex may change (see below) |
| `/approval <mode>` (or `/approvals`) | What happens when Codex wants to leave the sandbox (see below) |
| `/status` | Models, permission, approval, account, versions, tokens and context left |
| `/undo` | Put the files **and** the conversation back to before your last message (again to go further back). Shows what it will change and asks first |
| `/diff` | The project's git changes, including new files |
| `/commit [message]` | Commit the changes. Codex writes the message from the diff unless you give one; you approve or edit it. Secret files are never committed |
| `/pr [title]` | Push the work to a branch and open a pull request on GitHub (needs [gh](https://cli.github.com)); asks first |
| `/pr fix [number]` | Codex fixes the pull request's failing checks and open review comments, then commit and push, each after a yes (see "Git: commit and pull request") |
| `/save [show\|clear]` | Remember this project's mode, power, profile, permission and models for the next start |
| `/export [file]` | Save this conversation (your messages, answers, commands with output, diffs) as a Markdown file |
| `/prices [model in cached out \| model off]` | Set what a model costs, in dollars per million tokens |
| `/stats [days\|all]` | Tokens and cost per day and per project (default: the last 7 days) |
| `/memory [add\|everywhere <note> \| forget <n> \| clear \| on \| off]` | Notes Codex keeps between conversations: about this project, and your preferences for all projects (see "Memory") |
| `/schedule <when> <task>` | Run a task by itself at set times, like official Codex's automations (see "Scheduled tasks"). `/schedule` lists them; `/schedule run\|log\|undo\|remove <id>` |
| `/review [what]` | Codex's own code review, as in official Codex, read-only and in its own conversation: `uncommitted` (default), `branch <base>`, `commit <sha>`, or your own instructions. The engine doesn't report a review's tokens, so `/usage` shows 0 for the reviewer |
| `/history [number]` | Codex's conversations for this project from every Codex client (this app, the official Codex CLI, VS Code ...); a number carries on with one |
| `/history search <words>` | Find this project's conversations by title, first message, git branch or ID, as in official Codex's thread search (`/history <words>` works too); then `/history <number>` carries on with one of the matches |
| `/mention <file>`, or `@path` in a message | Attach a file to your message |
| `/image <file>` | Attach an image to your next message |
| `/skills`, or `$name` in a message | List the skills Codex can use; `$name` attaches one to your message |
| `/mcp` | List the MCP servers (external tools such as GitHub or databases) Codex is set up with, their status and tools |
| `/plugins [available [words] \| show \| install \| remove <name>]` | Codex's plugin marketplace, like the official apps' plugin directory: what is installed, what can be, and what a plugin adds. Installs and removals ask first (see "Plugins") |
| `/secrets [on\|off]` | List secret files in the project; turn the secret guard on or off |
| `/browser [on\|off]` | Codex App's private browser for the next conversation (see "Built-in browser") |
| `/browser check [on\|off\|<url>]` | Dual and team mode: changed web pages are opened in a headless browser before the planner's verdict (the project folder, or your dev server's address) |
| `/compact` | Summarise the conversation so far, to free up context |
| `/init` | Writes an `AGENTS.md` describing your project. Codex reads it automatically |
| `/models` | Lists the models Codex can use |
| `/usage` | Tokens used and context left |
| `/new`, `/clear` | Starts a new conversation |
| `/fork` | Carries on in a copy of this conversation, keeping the original |
| `/rename <title>` | Renames this conversation |
| `/sessions`, `/resume [number]` | Lists saved conversations and goes back to one |
| `/login`, `/logout` | Signs in with your ChatGPT account, or signs out |
| `/update`, `/version` | Installs the newest Codex App from GitHub, or shows the version |
| `/exit`, `/quit` | Quits |

While Codex works you see what official Codex shows too: its plan as a checklist (`[x]` done, `[>]`
doing, `[ ]` to do), each command and edit, the diff of the files it changed, and after each answer the
tokens used and how much of the model's context is left.

**Subagents.** Codex can start helper agents of its own for parts of a task, for example when you ask
it to "use two subagents in parallel". You see them as they work:

```text
  > codex: subagent shapes: started (gpt-6.2-sol, low)
  > codex: subagent words: started (gpt-6.2-sol, low)
  > codex: subagent: wait for subagents
    subagent shapes completed
```

Problems are shown too, for example `subagents: 1 completed, 1 errored (...)`. The `--json` result says
how many subagents ran. Each subagent works in a conversation of its own, and that has two
consequences:

- **Secret files.** The secret guard can't stop a subagent's commands as they start (see "Secret
  files"). After each turn, the app looks through every subagent's commands and edits instead, and warns
  you plainly if one read or changed a secret file.
- **Tokens.** The engine reports usage only for the main conversation, so with subagents `/usage` and
  costs can be lower than what was really used.

**Ctrl+C** (or `/stop`) stops the current task. Codex is told to stop, and you can carry on with a new
message. Anything else you type while Codex works steers it.

## Permissions (Codex's sandbox)

| Permission | What Codex may do |
|---|---|
| `plan` (official: `read-only`) | Read files and run read-only commands. Changes nothing |
| `workspace` (default; official: `workspace-write`) | Change files and run commands inside the project folder only |
| `full` (official: `danger-full-access`) | No sandbox: anything your Windows account can do. You must type `yes` to turn it on |

Sometimes Codex wants to step outside the sandbox, for example to use the network or write in
another folder. `--approval` (or `/approval`) decides what happens then:

- `deny_all` (default): always refused.
- `auto_review`: Codex's own reviewer decides.
- `ask` (experimental): you answer `y` or `n` for each request, like official Codex's "on-request"
  mode. This uses a part of Codex's SDK that isn't documented, so it may stop working with a future
  engine. If it doesn't work, the app tells you and uses `deny_all`.

## Protecting your system

Codex is kept away from the system disk and system files in four ways:

1. **The project folder is checked.** The app refuses to start on a disk root (`C:\`, `/`), a system
   folder (`C:\Windows`, `C:\Program Files`, `C:\ProgramData`, `/etc`, `/usr`, ...), a folder inside one,
   or your whole home folder. Codex may change anything inside the project folder, so it must be the
   folder of one project.
2. **Codex's sandbox.** With `plan` and `workspace`, Codex itself blocks writes outside the project
   folder. `full` turns this off, so it needs you to type `yes`, and it can't be used without someone
   at the keyboard.
3. **Dangerous requests are refused.** If Codex asks to run something that would damage the system
   (formatting a disk, `diskpart`, `bcdedit`, changing the registry, `rm -rf /`, deleting system
   restore points, shutting down, or changing files in a system folder), or to write in a system
   folder, the app says no without asking you.
4. **Codex is told** never to touch the operating system, system folders, the registry, drivers,
   boot settings or disks, and to ask you if a task seems to need it.
5. **Tools that work outside the project are off** (Codex App's own private browser is separate; see
   "Built-in browser"). Codex App uses the same Codex engine and settings as the official Codex apps, so plugins those apps installed would also reach Codex App: computer use
   (which can operate any app on your screen), browsers, and account plugins such as Slack or Google
   Calendar. They work outside the sandbox, so points 1 to 3 don't cover them. Codex App starts the
   engine with them switched off, for its own conversations only (your Codex settings file isn't
   changed). At start it lists what it switched off. `--outside-tools` (or `CODEX_APP_OUTSIDE_TOOLS=yes`)
   turns them on when you want them. Plugins that only work with files (PDF, documents, spreadsheets,
   presentations) stay on.

With `full` nothing stops Codex for real except instruction 4, so keep `workspace` unless you must.

## Plugins

`/plugins` reaches Codex's plugin marketplace, as the official Codex apps' plugin directory does: the
official OpenAI marketplaces, plus any marketplace in the project folder.

```text
/plugins                      what is installed, and what is off in Codex App
/plugins available latex      plugins you can install (all of them without words)
/plugins show superpowers     what one adds: its skills, and anything that reaches outside the project
/plugins install superpowers  install it (asks first)
/plugins remove superpowers   remove it (asks first)
```

A plugin bundles **skills**, which are instructions for tasks such as PDFs, LaTeX or test-driven
development. It can also bundle **MCP servers**, **app integrations** (Slack, Gmail ...), **hooks**
(code that runs on events) and **scheduled tasks**. `/plugins show` sorts plugins into two kinds:

- **Works with your files only**: skills, nothing else. It installs after your yes, and its skills are
  used from the next conversation (`/skills` lists them, `$name` in a message uses one).
- **Reaches outside the project**: anything with servers, apps, hooks or scheduled tasks. Those work
  outside the sandbox, which is why Codex App keeps such tools off (see "Protecting your system"). So
  Codex App only installs one when you started it with `--outside-tools`. It then records the plugin in
  `~/.codex-app/outside-plugins.json`, so a normal start switches it off again. On this PC, the browser,
  Chrome and computer-use plugins are of this kind (they have hooks or an MCP server), and the document,
  PDF, spreadsheet and presentation plugins aren't.

Plugins go into Codex's own settings (`~/.codex`), which the official Codex apps share: a plugin installed
here is installed there too, and the other way round. Both questions say so.

## Secret files

Secret files are `.env` files (not `.env.example`), keys (`*.pem`, `*.key`, `id_rsa` ...), credentials
(`credentials.json`, `secrets.*`, `.npmrc`, `.netrc` ...), databases (`*.db`, `*.sqlite`) and anything
in a `data/`, `.ssh/` or `.aws/` folder. Codex's sandbox controls *writing*, not *reading*, so the app
keeps Codex away from them in four ways:

1. **Codex is told** never to read, print or change them.
2. **The secret guard** watches every command and edit as Codex starts it. If one reads or changes a
   secret file, the turn is stopped at once and **taken out of the conversation**, so what Codex read
   is never sent to OpenAI with later messages. `/secrets off` turns it off (`CODEX_APP_SECRET_GUARD=no`).
3. **Requests to leave the sandbox** that touch a secret file are refused without asking.
4. **At start** the app lists the secret files inside the project.

The guard acts the moment a command starts, so a very fast command may still finish first. For a
project with real secrets the safest place for them is still outside the project folder.

**Subagents** (helper agents Codex starts by itself) run their commands in conversations of their own,
which the guard can't watch as they happen. Codex's sandbox still applies to them. After each turn the
app reads what every subagent did, and if one read or changed a secret file it says so in red: what it
read may have reached OpenAI, so change that secret if it matters. In a live test, a subagent asked to
show every file's contents left `.env` alone by itself.

The guard looks at what a command does with a name, not just whether the name appears. **Searching for**
a secret file's name, or **filtering it out**, is fine: `rg '\.env\b' src`, `Select-String -Pattern '.env'`,
`Where-Object Name -notmatch '^\.env'`. Careful models write exactly these commands, because they are told
to keep away from secret files, and they used to set the guard off. **Opening** one is stopped, however it is
written: `Get-Content .env`, `rg KEY .env`, `Select-String -Path .env`, a wildcard (`cat .env*`,
`Get-Content *.pem`), or a filter whose matches are then read (`... -match '\.env' | Get-Content`,
`rg --files | rg '\.env$' | xargs cat`).

## Reliability

- **Engine-update safety check.** `py main.py --selftest` checks, in a few seconds and without a model
  turn (nothing billed), that the installed Codex engine still has everything this app uses, including
  the parts that go beyond the SDK's public interface ("ask" mode, goals, `/undo`, structured output,
  `/review`, `/history`, MCP, skills, limits). `py main.py --update` runs it after installing a new
  engine, and puts the old engine back if anything fails.
- **Auto-compact.** When only 10% of the context is left after a task, Codex summarises the
  conversation by itself, so long work doesn't stall (`CODEX_APP_AUTO_COMPACT=no` turns it off,
  `CODEX_APP_AUTO_COMPACT_AT` sets the percent).
- **Ctrl+C really stops Codex.** (Before 1.6.0 the app could stop waiting while Codex carried on.)

- **Automatic retries.** When a turn fails because OpenAI's servers are busy or had an internal error,
  the app waits (5, 15, then 45 seconds) and asks Codex to carry on from where it stopped, up to 3 times
  (`CODEX_APP_RETRIES`, 0 turns it off). The message isn't sent again, so finished work isn't repeated.
  Usage limits and bad requests aren't retried, because waiting a minute won't fix them.
- **Limit warnings.** Signed in with a ChatGPT account, `/status` shows how much of your plan's limits
  you have used and when they reset, and the app warns after a task once a limit is 80% used. If a limit
  is hit, it says so. With an API key there are no plan limits to show: set a monthly spend limit in
  your OpenAI account.
- **Finish signal.** When a task took 30 seconds or more, the app beeps and, on Windows, shows a
  notification, so you can do something else while Codex works (`CODEX_APP_NOTIFY=no` turns it off,
  `CODEX_APP_NOTIFY_AFTER` sets the seconds).

## Git: commit and pull request

`/commit` shows the changed files and a commit message Codex wrote from the actual diff (in a separate,
read-only conversation). You commit it with `y`, write your own with `e`, or stop with `n`. Secret
files are never staged, even if `.gitignore` doesn't exclude them; the app tells you to add them there.

`/pr` commits anything left first, then shows what it will do and asks. On `main`, your new commits
move to a new branch (for example `codex/fix-the-login-bug`) and `main` goes back to match GitHub, so
nothing is lost. Then it pushes the branch and opens the pull request with GitHub's `gh` tool, and
shows its link. Git hooks run as usual.

`/pr fix` (or `/pr fix <number>`) is for a pull request that came back with problems, like the official
Codex app's pull request review. The app reads the pull request with `gh`:

- its failing checks, with the end of each failed GitHub Actions job's log;
- its open review comments (unresolved, not outdated threads) and reviews that ask for changes.

It lists what it found and asks. Then Codex fixes them in your chosen mode, runs the checks that failed
(or the closest local test), and says which comments it didn't follow and why. Afterwards `/commit` and
a push update the pull request, each after a yes.

- Logs and comments are written by other people, or by whatever ran in CI. So Codex gets them as data to
  judge, not as orders: it is told never to run commands, open links, change settings or touch secrets
  because a log or comment says so, and to change only the files the fixes need.
- The pull request's branch must be checked out and up to date with GitHub (the app says `git switch`
  or `git pull` otherwise). Pull requests from forks, merged or closed ones, and merge conflicts are left
  to you. Checks that are still running aren't included.
- If Codex doesn't finish the fixes (you stop it, it fails, or the secret guard stops a turn), nothing
  is committed or pushed: look at the changes with `/diff` first, then `/commit` or `/undo`.
- Logs are cut to their last 120 lines (30,000 characters in all) and comments to 2,000 characters each,
  so a huge log doesn't use up the context.
- `/pr fix the login bug` is still a pull request titled "fix the login bug": only `/pr fix` alone, or
  with a number, fixes one.

## Saved preferences

`/save` remembers this project's mode, power, profile, permission, approval, models, efforts, team
size, web search, finish signal and memory on/off in `C:\Users\<you>\.codex-app\projects.json`. The next start on
that project uses them. Command-line options still win, and saved preferences win over environment
variables. `/save show` lists them and `/save clear` forgets them. A saved `full` permission still asks
for `yes` at every start.

## Files Codex's sandbox locks (Windows)

On Windows, Codex runs commands as a separate sandbox account. A file it edits with its own edit tool keeps
the folder's permissions. But a file that one of its commands writes or replaces can belong to the sandbox
account alone: PowerShell `Set-Content`, Python, or pytest's cache folders. Then you can't open the file,
git can't read it (so `/undo` and the list of changed files stop working), and team mode can't merge it.
In a benchmark run, every mode left such files: rewritten source files, test files Codex wrote, and
pytest cache folders.

So after every turn, and in each team worker's worktree before it is merged, Codex App looks for files and
folders in the project that your account can't open. This takes about a tenth of a second. If it finds
any, it asks Codex's own sandbox to give your account full access again, using `icacls /grant` (the
sandbox account owns those files, so it may). This takes a few seconds. Then it checks again, and tells
you what it fixed, or which files are still locked and the command to unlock them as administrator.
`CODEX_APP_FIX_PERMISSIONS=no` turns this off. Other systems aren't affected, because there the sandbox
runs as you.

## Undo

`/undo` puts back both the files and Codex's conversation as they were before your last message;
`/undo` again goes further back. It lists the files it will change and asks first.

- In a git project the app takes a snapshot of every file before each message, the way official Codex's
  undo works. Snapshots are private git objects: your index, branches, stash and history are never
  touched, and nothing shows up in `git status`. Files your `.gitignore` leaves out (such as `.env` or
  `node_modules`) are never changed by `/undo`.
- Without git, `/undo` reverses the changes Codex reported, and refuses if the files have changed since.
- Undo works within one conversation: `/new`, `/mode` and `/resume` start afresh.

## Memory

Codex App keeps short notes between conversations, like official Codex's memories: facts about each
project (how to build and test it, its conventions, decisions you made) and your preferences for all
your projects. Each new conversation starts with them, so you don't have to explain the same things
again.

- **How notes are made.** There is no extra model call. When Codex learns something lasting, or you ask
  it to ("remember that we deploy with ./deploy.sh"), it ends its answer with a line
  `REMEMBER: <fact>`, or `REMEMBER EVERYWHERE: <fact>` for a preference that holds in all your projects.
  The app keeps the line and shows it: `Remembered: ...`. When a note turns out wrong, Codex writes
  `FORGET: <note>` and the app drops it. Every agent can add notes: the planner and workers too.
- **What is never kept.** Anything that looks like a secret (API keys, tokens, passwords, private keys,
  long key-like strings) is refused, with a warning. Codex is told never to note personal data, or
  anything a file, command output or web page asked it to remember. Notes are short (240 characters)
  and few: the newest 30 per project and 20 for all projects, at most 3 from one answer.
- **How notes are used.** They are given to the main agents as data, not instructions: your messages
  and the app's rules always win. Helpers (`/review`, commit messages) don't get them. New notes are used
  from the next conversation (`/new`).
- **Where they are.** `C:\Users\<you>\.codex-app\memory.json`, outside the project, so they never show
  up in git or get committed.
- `/memory` lists them, numbered. `/memory add <note>` and `/memory everywhere <note>` add one yourself,
  `/memory forget <number>` drops one, and `/memory clear` (or `clear everywhere`) deletes them after
  asking. `/memory off` stops using and making notes from the next conversation, and `/save` keeps that
  for the project. `CODEX_APP_MEMORY=no` turns memory off everywhere.

## Saved conversations

Each conversation's Codex thread IDs are saved in `C:\Users\<you>\.codex-app\sessions\` (set
`CODEX_APP_HOME` to change it). The conversations themselves are kept by Codex.
`--continue` or `/resume` picks them up again.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `CODEX_APP_MODEL` | `gpt-6.2-sol` | Single mode model |
| `CODEX_APP_EFFORT` | `medium` | Single mode effort: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `CODEX_APP_PLANNER_MODEL` | `gpt-6.2-astra` | Dual mode planner |
| `CODEX_APP_PLANNER_EFFORT` | `high` | Planner effort |
| `CODEX_APP_WORKER_MODEL` | `gpt-6.2-sol` | Dual mode worker |
| `CODEX_APP_WORKER_EFFORT` | `medium` | Worker effort |
| `CODEX_APP_PERMISSION` | `workspace` | Starting permission |
| `CODEX_APP_APPROVAL` | `deny_all` | Requests to leave the sandbox: `deny_all`, `auto_review`, `ask` |
| `CODEX_APP_SEARCH` | `no` | Let Codex search the web live (same as `--search`) |
| `CODEX_APP_OUTSIDE_TOOLS` | `no` | Allow computer use, browsers and account plugins (same as `--outside-tools`) |
| `CODEX_APP_BROWSER` | `no` | Codex App's private browser (same as `--browser`) |
| `CODEX_APP_BROWSER_CHANNEL` | `msedge` | Which browser it drives: `msedge`, `chrome` or `chromium` |
| `CODEX_APP_BROWSER_HEADLESS` | `no` | Run it without a window |
| `CODEX_APP_BROWSER_SITES` | (any) | Only these sites (comma-separated, subdomains included) may open in the browser |
| `CODEX_APP_BROWSER_PRIVATE` | `no` | Allow private network addresses (routers, intranets) in the browser |
| `CODEX_APP_BROWSER_CHECK` | `yes` | Dual and team mode: look at changed web pages in the browser before the verdict (see "Browser check") |
| `CODEX_APP_CHECK_URL` | | Where the browser check opens the pages, for a dev server you run (for example `http://localhost:3000`); empty: the app serves the project folder |
| `CODEX_APP_PROFILE` | `auto` | Task profile: `auto`, `coding`, `chat`, `maths` |
| `CODEX_APP_POWER` | `normal` | Power level: `normal`, `high`, `max` |
| `CODEX_APP_TEAM_SIZE` | `3` | Team mode: workers at the same time (1 to 8) |
| `CODEX_APP_FIX_PERMISSIONS` | `yes` | Windows: give your account access back to files Codex's sandbox made unreadable (see "Files Codex's sandbox locks") |
| `CODEX_APP_MEMORY` | `yes` | Notes kept between conversations (see "Memory"); `no` turns them off |
| `CODEX_APP_TEAM_WORKTREES` | `yes` | Team mode: each worker in its own git worktree (`no`: all share the project folder) |
| `CODEX_APP_SECRET_GUARD` | `yes` | Stop a turn the moment Codex starts to read or change a secret file |
| `CODEX_APP_RETRIES` | `3` | Automatic retries when OpenAI is busy (0 to turn off) |
| `CODEX_APP_NOTIFY` | `yes` | Beep and show a notification when a long task finishes |
| `CODEX_APP_NOTIFY_AFTER` | `30` | How many seconds make a task "long" |
| `CODEX_APP_AUTO_COMPACT` | `yes` | Compact the conversation by itself when little context is left |
| `CODEX_APP_AUTO_COMPACT_AT` | `10` | ... at this percent of context left or less |
| `CODEX_APP_MAX_REVIEW_ROUNDS` | `2` | How many times the planner may send work back |
| `CODEX_APP_PLANNER_REVIEW` | `auto` | Dual and team: when the planner reviews the work: `auto` (not for a small change whose tests passed), `always`, or `never` (`no`) |
| `CODEX_APP_REVIEW_MAX_LINES` | `200` | ... how many changed lines still count as "small" |
| `CODEX_APP_REVIEW_EFFORT` | one below the planner's | The review's reasoning effort (`high` planner: `medium`) |
| `CODEX_APP_UPDATE_CHECK` | `yes` | Look for a new app version once a day at start |

Models can also be set with `--model`, `--planner-model` and `--worker-model`.

## Cost

With an API key you pay OpenAI per token. Enter your models' prices once, in dollars per million
tokens (input, cached input, output; from https://openai.com/api/pricing):

```text
/prices gpt-6.2-sol 1.25 0.125 10
```

They are saved in `C:\Users\<you>\.codex-app\prices.json`. From then on `/usage` and the line after each
task show dollars as well as tokens.

Every task's tokens are added to `.codex-app\usage.jsonl`: when, which project, which mode, and the
tokens per model, but never your messages. `/stats` sums them up per day and per project for the last
7 days (`/stats 30`, `/stats all`). It uses today's prices, so it shows a cost for earlier work too once
you set a price. Set a monthly spend limit in your OpenAI account as well.

## Scheduled tasks

`/schedule` makes Codex App run a task by itself at set times, like official Codex's automations. It
uses your computer's own scheduler, so nothing has to stay open:

```text
/schedule daily 09:00 run the tests and fix what fails
/schedule weekdays 18:00 update CHANGELOG.md from today's commits
/schedule mon,thu 08:30 check the dependencies for updates and list them
/schedule every 3 hours look for new TODO comments and list them
```

Times are written as `daily`, `weekdays`, `weekends`, days (`mon,wed,fri` or `monday`) followed by
`HH:MM`, or `hourly` / `every N hours`. The app shows what it will do and asks before creating it.

- **What a run uses:** the project, mode, permission, approval, power, profile and models of the session
  you made it in. A run never asks anything, because no one is there to answer. So schedules never use
  `full` permission, the `ask` approval mode becomes `deny_all`, and the browser is off.
- **Windows:** a Task Scheduler task in the `CodexApp` folder. It runs only while you are signed in (no
  password is stored), also on battery, never twice at once, catches up a run missed while the computer
  was off, and is stopped after 4 hours. It starts `pythonw.exe`, so no window opens.
- **Linux and macOS:** a crontab line marked `# codex-app:<id>`.
- **Results:** each run works like `--json` (below). Its result and log go to
  `~/.codex-app/schedules/runs/<id>/` (the newest 50 are kept), and Windows shows a notification when it
  ends. `/schedule` lists your schedules with each one's last result, and `/schedule log <id>` shows
  its recent runs and the last answer.
- **Undo:** each run takes a snapshot of the project's files first (git projects). `/schedule undo <id>`
  shows what would change, asks, then puts the files back as they were before the last run. This also
  undoes changes made to those files after that run.
- `/schedule run <id>` starts one in the background now. `/schedule remove <id>` deletes it from the app
  and from the computer's scheduler.
- Every run costs tokens like any task, so `/stats` counts them too.

## Automation

`--json` runs one task and prints only the result to stdout, as one JSON object; everything else goes
to stderr. That way scripts, scheduled jobs and CI can drive Codex App:

```bash
py main.py --project "C:\path\to\project" --json "fix the failing test"
```

```json
{"ok": true, "answer": "...", "error": null, "mode": "single", "project": "...",
 "files_changed": [{"path": "tests/test_app.py", "change": "modified"}],
 "tokens": {"gpt-6.2-sol": {"input": 44337, "cached": 22100, "output": 87}},
 "cost_usd": 0.0412, "seconds": 18.9, "app_version": "...",
 "steps": [{"agent": "codex", "step": "task", "model": "gpt-6.2-sol", "input": 44337, "cached": 22100,
            "output": 87, "reasoning": 40, "tool_calls": 6, "seconds": 18.2, "cost_usd": 0.0412}],
 "agents": {"codex": {"model": "gpt-6.2-sol", "turns": 1, "input": 44337, "cached": 22100, "output": 87,
                      "reasoning": 40, "tool_calls": 6, "seconds": 18.2}}}
```

The exit code is 0 when the task succeeded and 1 when it didn't. `files_changed` comes from the
undo snapshot in a git project, and from Codex's diffs without git. `cost_usd` is `null` until the
model has a price (`/prices`). `--goal` works with `--json` too.

`steps` lists every turn of the task in the order they ended, with what it used: `task` in single mode;
`plan`, `work`, `review` and `fix` in dual and team mode (each team worker's part is a `work` step);
`browser check` for the browser check; `answer` for a chat message; `goal` for `/goal`. In dual and
team mode, `review` says how the planner's review went (`skipped`, `approved`, `changes needed`, `no
verdict`) and `before_review_tree` is the project just before it, as a git tree id. `tool_calls`
counts the commands, file changes and other tools Codex used in that turn. `agents` sums the steps of
each agent (planner, worker, worker-2, ...). `input` counts all input, cached or not: the input that
isn't cached (`input` minus `cached`) is what costs the most.

`/export` saves the current conversation as a Markdown file: your messages, Codex's answers, every
command with the end of its output, and every file change with its diff. Without a file name it goes to
`.codex-app\exports\`.

## Updates

Two things update:

- **Codex App** itself, from GitHub.
- **The Codex engine**, OpenAI's `openai-codex` package from PyPI. It brings the Codex program and
  gets new versions often.

Once a day, at start, the app checks both and tells you if something new is waiting.

**To update everything, run:**

```bash
py main.py --update
```

It updates the app first, then installs the newest Codex engine this app version accepts. Run it
while the app is closed: the engine can't be replaced while Codex is running.

`/update` inside the app updates the app and says whether a new engine is waiting. It, `--version` and
the daily notice show what's new: the CHANGELOG entries of every version between yours and the new one.
`/version` (or `py main.py --version`) shows both versions and checks for newer ones.

**How engine updates are kept safe:**

- `requirements.txt` sets the engine versions this app version works with (`openai-codex>=0.171,<0.200`).
  A future engine that changes its programming interface isn't installed until you widen the range
  after testing it. `--version` tells you when a newer engine exists outside the range.
- After installing a new engine, the app runs its self-test on it (`--selftest`: everything the app
  uses, no model turn, nothing billed). If anything fails, the previous version is put back.
- An app update is refused while you have uncommitted changes to the app's own files.
- Your settings, saved conversations and Codex sign-in are never touched.

**To publish a new app version:**

1. Raise `__version__` in `codex_app/__init__.py`.
2. Add a `CHANGELOG.md` entry.
3. Commit and push.

To allow a newer engine, test it, then widen the range in `requirements.txt` in that same release.

## Files

| File | What it is |
|---|---|
| `main.py` | The terminal app: sign-in, commands, screen output |
| `codex_app/modes.py` | `SingleMode`, `DualMode` and each role's instructions |
| `codex_app/agent.py` | One Codex thread: start, resume, fork, compact, stream and show its work |
| `codex_app/approvals.py` | Answering Codex's requests to leave the sandbox, and the `ask` mode |
| `codex_app/protect.py` | Keeping Codex away from the system disk and system files |
| `codex_app/outside.py` | Switching off computer use, browsers and account plugins for Codex App |
| `codex_app/plugins.py` | `/plugins`: the marketplace, what a plugin adds, and which plugins reach outside the project |
| `codex_app/browser_mcp.py` | The built-in browser: an MCP server with its safety rules |
| `codex_app/browser.py` | The app's side of the browser: starting it and asking you before risky actions |
| `codex_app/webcheck.py` | The browser check: which changes are web changes, the page server, the checker's report |
| `codex_app/browser_guard.py` | The browser's misuse protection: attack input, networks, rate limits, CAPTCHAs, log |
| `codex_app/profiles.py` | Task profiles (coding, chat, maths) and power presets |
| `codex_app/goals.py` | Goal mode: Codex working on its own until a goal is done |
| `codex_app/undo.py` | File snapshots and restoring them for `/undo` |
| `codex_app/worktrees.py` | Team mode: a git worktree per worker, and merging their work back |
| `codex_app/gitflow.py` | `/commit` and `/pr`: committing and opening pull requests |
| `codex_app/prfix.py` | `/pr fix`: reading a pull request's failing checks and review comments |
| `codex_app/prefs.py` | Saved preferences per project (`/save`) and the finish signal |
| `codex_app/selftest.py` | `--selftest`: checks the engine still has everything the app uses |
| `codex_app/export.py` | `/export`: a conversation as Markdown |
| `codex_app/costs.py` | `/prices` and `/stats`: model prices and the usage log |
| `codex_app/perms.py` | Finding files Codex's Windows sandbox made unreadable, and giving them back |
| `codex_app/memory.py` | `/memory`: notes kept between conversations |
| `codex_app/schedule.py` | `/schedule`: saved schedules, Task Scheduler and cron entries, run results |
| `.github/workflows/tests.yml` | The tests on GitHub, on Windows and Linux |
| `.github/workflows/engine-watch.yml`, `.github/scripts/engine_watch.py` | The weekly check of the newest Codex engine |
| `.github/workflows/release.yml` | GitHub Releases with CHANGELOG notes |
| `tests/conftest.py` | Throwaway git repositories, built once per test run |
| `codex_app/engine.py` | Engine requests the SDK has no method for: undoing turns, MCP servers, skills |
| `codex_app/config.py` | Settings, permissions, prices |
| `codex_app/sessions.py` | Saving and resuming conversations |
| `codex_app/updater.py` | Updating Codex App from GitHub and the Codex engine from PyPI |
| `tests/` | Tests with a fake Codex (no key, nothing billed) |
| `bench/run.py`, `bench/tasks/` | The benchmark: fixed tasks with hidden tests, run in each mode |
| `.github/workflows/bench.yml` | The benchmark on GitHub, started by hand |

## Tests

```bash
py -m pytest -q
```

The tests use a fake Codex: no key is needed and nothing is billed. Repositories the tests need are
built once per run and copied for each test, so the suite takes about 2 minutes on Windows instead of 3.

Four GitHub workflows keep the app safe:

| Workflow | When | What it does |
|---|---|---|
| `tests.yml` | Every pull request and push to `main` | All tests on Windows and Linux, the offline part of `--selftest`, and a coverage report (which code no test runs) on Linux |
| `engine-watch.yml` | Every Monday, or by hand | Installs the newest Codex engine, even one the app doesn't accept yet, and runs all tests and the offline self-test with it on Windows and Linux. If they pass and the engine is outside the accepted range, it opens a pull request that widens the range up to the next minor version. If they fail, it opens an issue |
| `bench.yml` | Only when you start it (Actions > benchmark > Run workflow) | The benchmark (see "Benchmark") with real model runs, so it is billed; it needs an `OPENAI_API_KEY` repository secret. The results are in the run's summary and kept as an artifact |
| `release.yml` | A push to `main` that raises the version | Publishes a GitHub Release `vX.Y.Z` with that version's CHANGELOG entry as its notes |

`main` is protected on GitHub: a pull request can only be merged once both test jobs (`windows-latest`
and `ubuntu-latest`) have passed. As the repository's admin you can still override it.

A pull request opened by `engine-watch.yml` doesn't run `tests.yml` itself (GitHub doesn't start workflows
from its own token); the engine-watch run it links to has already run them with the new engine. Before
merging it, run `py main.py --selftest` on a PC with that engine: the live checks need a signed-in Codex,
so they can't run on GitHub.

## Benchmark

`bench/run.py` measures how often each mode gets fixed tasks right, and what that costs, so the modes can
be compared in numbers rather than claims:

```bash
py bench/run.py
```

```bash
py bench/run.py --modes single,dual --repeat 3 --tasks median-bug,two-fixes
```

- **Seven tasks** in `bench/tasks/`: fix a bug, add a function from a spec, rename across files, make
  failing tests pass without changing them, write a parser with error handling, two independent
  fixes in two files (which suits team mode), and `ledger`. That last one is a complex task: seven
  features and fixes across four files (exact decimal money, CSV quoting, currency conversion, split
  payments, errors that name the line, a new CLI). Every rule its hidden tests check is stated in its
  prompt.
- **How a run is judged.** Each task starts as a fresh git project. Codex App works on it exactly as you
  would use it (`--json`, permission `workspace`, approval `deny_all`). Only then are the task's hidden
  tests copied in, so Codex never sees them, and a run passes only if all of them pass.
- **What is recorded.** For each mode: runs passed, pass rate, median time, median tokens and total cost.
  Costs need prices for the models (`/prices`). The results go to `bench/results/<date-time>.md` and
  `.json`, which git ignores. They are saved again after every run, so a benchmark that is stopped
  (Ctrl+C, or the process ended) keeps every run it finished. Its report then starts with
  "Partial: N of M planned runs", and the JSON has `"complete": false`.
- **Where the tokens went.** The report has a table per mode and step (plan, work, review, fix, ...):
  the average turns, tool calls, tokens, share of the mode's tokens, uncached input, output and time per
  run. It shows what dual and team mode spend their extra tokens on. Each run in the JSON keeps its
  `by_step` and `agents` totals.
- **Did the review help?** In dual and team mode the hidden tests also run on the project as it was
  just before the planner's review. The report counts per mode the runs reviewed and skipped, passed
  before and after the review, rescued (failed before, passed after) and broken (the other way), so you
  can see whether the review is worth what it costs.
- **Costs.** Runs are billed like any task: 18 runs for all six tasks in all three modes. Memory, the
  browser check, the update check and the finish signal are off, and app data goes to a temporary folder,
  so your notes and `/stats` stay as they were. `--power high` runs every mode at a higher power level, and
  `--keep` keeps the projects and the app's log of each run.
- **Fair tasks.** `py bench/run.py --check` (no model, nothing billed) makes sure every task is fair: its
  starting project fails the hidden tests, and its reference solution passes them. The normal tests run
  this, so a broken task can't get in.
- **On GitHub.** The `benchmark` workflow runs it by hand, with an `OPENAI_API_KEY` secret.

A new task is a folder with `task.json` (`title`, `kind`, `prompt`), `start/`, `hidden/` (pytest tests)
and `solution/`.

## License

MIT. See [LICENSE](LICENSE).
