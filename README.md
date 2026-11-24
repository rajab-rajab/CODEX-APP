# Codex App

OpenAI's **Codex** ([github.com/openai/codex](https://github.com/openai/codex), Apache 2.0) in your
terminal, with two ways of working. It uses OpenAI's official Codex Python SDK (`openai-codex`),
which installs the real Codex engine. So the reading, editing, command running and sandboxing are
all done by Codex itself. This app adds the two modes, saved conversations and self-updates.

## Two modes

| Mode | Who does what | Models (default) |
|---|---|---|
| **A) Single model** | One Codex thread reads the code, makes the changes and checks them | `gpt-6.2-sol` |
| **B) Dual model** | A **planner** reads the code and writes a plan. A **worker** carries it out. The planner then checks the files and runs the tests itself, and either approves or sends the work back with fixes (up to 2 times) | Planner `gpt-6.2-astra`, worker `gpt-6.2-sol` |

In dual mode:

- The planner always works in Codex's **read-only sandbox**. It can read files and run tests, but
  it can't change anything.
- A question that needs no change is answered by the planner alone, and the worker isn't called.

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
| `--mode single\|dual` | A) single model or B) planner + worker |
| `--model`, `-m`, `--effort` | Single mode model and reasoning effort |
| `--planner-model`, `--worker-model` | Dual mode models |
| `--permission`, `--sandbox`, `-s` | `plan`, `workspace`, `full`, or official Codex's `read-only`, `workspace-write`, `danger-full-access` |
| `--approval`, `--ask-for-approval`, `-a` | `deny_all`, `auto_review` or `ask` (see below) |
| `--image`, `-i` | Attach an image (more than once for several) |
| `--search` | Let Codex search the web live |
| `--config`, `-c` | A Codex setting, `-c key=value`, exactly as in official Codex |
| `--continue` | Carry on with the last conversation |

`-c` now means `--config`, as in official Codex. In 1.1.1 and earlier it was short for `--continue`.

## Commands

| Command | What it does |
|---|---|
Most of these are the same as official Codex's.

| Command | What it does |
|---|---|
| `/mode single`, `/mode dual` | Switch mode (starts a new conversation) |
| `/model [name [effort]]` | Show or switch the model and reasoning effort, from the next message on. Dual mode: `/model planner <name>` or `/model worker <name>` |
| `/permission <mode>` (or `/permissions`) | What Codex may change (see below) |
| `/approval <mode>` (or `/approvals`) | What happens when Codex wants to leave the sandbox (see below) |
| `/status` | Models, permission, approval, account, versions, tokens and context left |
| `/diff` | The project's git changes, including new files |
| `/review [what]` | A read-only code review in its own conversation: `uncommitted` (default), `branch <base>`, `commit <sha>`, or your own instructions |
| `/mention <file>`, or `@path` in a message | Attach a file to your message |
| `/image <file>` | Attach an image to your next message |
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

**Ctrl+C** stops the current task. Codex is told to stop, and you can carry on with a new message.

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

With `full` nothing stops Codex for real except instruction 4, so keep `workspace` unless you must.

## Secret files

Codex is told never to read or change `.env` files (except `.env.example`), keys, databases or
`data/` folders, which matters for your site. This is an instruction to Codex, not a hard block.
Codex's sandbox controls *writing*, not *reading*. For a project that holds real secrets, keep
them out of the project folder, or use Claude App, which blocks those files in code.

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
| `CODEX_APP_MAX_REVIEW_ROUNDS` | `2` | How many times the planner may send work back |
| `CODEX_APP_UPDATE_CHECK` | `yes` | Look for a new app version once a day at start |

Models can also be set with `--model`, `--planner-model` and `--worker-model`.

## Cost

With an API key you pay OpenAI per token. `/usage` shows the tokens. To see dollars too, add your
models' prices to `PRICES` in `codex_app/config.py`. Set a monthly spend limit in your OpenAI account.

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

`/update` inside the app updates the app and says whether a new engine is waiting.
`/version` (or `py main.py --version`) shows both versions and checks for newer ones.

**How engine updates are kept safe:**

- `requirements.txt` sets the engine versions this app version works with (`openai-codex>=0.171,<0.200`).
  A future engine that changes its programming interface isn't installed until you widen the range
  after testing it. `--version` tells you when a newer engine exists outside the range.
- After installing a new engine, the app starts it once to check it works. If it doesn't, the
  previous version is put back.
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
| `codex_app/config.py` | Settings, permissions, prices |
| `codex_app/sessions.py` | Saving and resuming conversations |
| `codex_app/updater.py` | Updating Codex App from GitHub and the Codex engine from PyPI |
| `tests/` | Tests with a fake Codex (no key, nothing billed) |

## Tests

```bash
py -m pytest -q
```

## License

MIT. See [LICENSE](LICENSE).
