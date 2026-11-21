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

`codex-app.cmd` starts it too. Add the folder to your PATH to type `codex-app --mode dual` from anywhere.

## Commands

| Command | What it does |
|---|---|
| `/mode single`, `/mode dual` | Switch mode (starts a new conversation) |
| `/permission plan\|workspace\|full` | What Codex may change (see below) |
| `/init` | Writes an `AGENTS.md` describing your project. Codex reads it automatically |
| `/models` | Lists the models Codex can use |
| `/usage` | Tokens used |
| `/sessions`, `/resume [number]` | Lists saved conversations and goes back to one |
| `/clear` | Starts a new conversation |
| `/login` | Signs in with your ChatGPT account |
| `/update`, `/version` | Installs the newest Codex App from GitHub, or shows the version |
| `/exit` | Quits |

**Ctrl+C** stops the current task. Codex is told to stop, and you can carry on with a new message.

## Permissions (Codex's sandbox)

| Permission | What Codex may do |
|---|---|
| `plan` | Read files and run read-only commands. Changes nothing |
| `workspace` (default) | Change files and run commands inside the project folder only |
| `full` | No sandbox: anything your Windows account can do. Use only if you must |

Sometimes Codex wants to step outside the sandbox, for example to use the network. `--approval`
decides what happens then:

- `deny_all` (default): always refused.
- `auto_review`: Codex's own reviewer decides.

Codex App has no "ask me before each change" mode, because Codex's SDK doesn't offer one. Use
`plan` to look first, then `workspace` to do the work.

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
| `CODEX_APP_EFFORT` | `medium` | Single mode effort: `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `CODEX_APP_PLANNER_MODEL` | `gpt-6.2-astra` | Dual mode planner |
| `CODEX_APP_PLANNER_EFFORT` | `high` | Planner effort |
| `CODEX_APP_WORKER_MODEL` | `gpt-6.2-sol` | Dual mode worker |
| `CODEX_APP_WORKER_EFFORT` | `medium` | Worker effort |
| `CODEX_APP_PERMISSION` | `workspace` | Starting permission |
| `CODEX_APP_APPROVAL` | `deny_all` | Requests to leave the sandbox |
| `CODEX_APP_MAX_REVIEW_ROUNDS` | `2` | How many times the planner may send work back |
| `CODEX_APP_UPDATE_CHECK` | `yes` | Look for a new app version once a day at start |

Models can also be set with `--model`, `--planner-model` and `--worker-model`.

## Cost

With an API key you pay OpenAI per token. `/usage` shows the tokens. To see dollars too, add your
models' prices to `PRICES` in `codex_app/config.py`. Set a monthly spend limit in your OpenAI account.

## Updates

Like Claude App, it checks GitHub once a day and updates with `/update` or `py main.py --update`.
It never updates over uncommitted changes to its own files.

To publish a new version:

1. Raise `__version__` in `codex_app/__init__.py`.
2. Add a `CHANGELOG.md` entry.
3. Commit and push.

The Codex engine itself updates with `py -m pip install -U openai-codex`.

## Files

| File | What it is |
|---|---|
| `main.py` | The terminal app: sign-in, commands, screen output |
| `codex_app/modes.py` | `SingleMode`, `DualMode` and each role's instructions |
| `codex_app/agent.py` | One Codex thread: start, resume, stream and show its work |
| `codex_app/config.py` | Settings, permissions, prices |
| `codex_app/sessions.py` | Saving and resuming conversations |
| `codex_app/updater.py` | Updating Codex App from GitHub |
| `tests/` | Tests with a fake Codex (no key, nothing billed) |

## Tests

```bash
py -m pytest -q
```

## License

MIT. See [LICENSE](LICENSE).
