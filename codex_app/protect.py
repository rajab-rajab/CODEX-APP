"""Keeping Codex away from the system disk and system files.

Three checks:
- check_project(): refuses a project folder that is a disk root, a system folder, inside one, or your
  whole home folder. Codex's sandbox lets Codex write anywhere inside the project folder, so this
  folder must never be one that holds the operating system.
- is_system_path(): whether a path is inside a system folder.
- dangerous_command(): whether a command would damage the system (formatting disks, deleting
  system folders, changing the registry or boot settings, ...). Requests to run one are refused
  without asking (see approvals.py)."""

import os
import re
import sys
from pathlib import Path
from typing import Optional

_POSIX_SYSTEM = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/proc", "/root",
                 "/run", "/sbin", "/snap", "/sys", "/usr", "/var/lib", "/var/log", "/var/cache",
                 "/var/spool", "/var/run", "/var/db", "/System", "/Library")


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _inside(child: str, parent: str) -> bool:
    parent = parent.rstrip("\\/")
    return child == parent or child.startswith(parent + os.sep)


def system_dirs() -> list[str]:
    """The folders that hold the operating system and installed programs."""
    if os.name == "nt":
        env = os.environ
        drive = env.get("SystemDrive", "C:") + "\\"
        dirs = [env.get("SystemRoot") or drive + "Windows", env.get("ProgramFiles"), env.get("ProgramFiles(x86)"),
                env.get("ProgramW6432"), env.get("ProgramData"), drive + "$Recycle.Bin",
                drive + "System Volume Information", drive + "Recovery", drive + "Boot"]
    else:
        dirs = list(_POSIX_SYSTEM)
    return sorted({_norm(d) for d in dirs if d})


def _is_disk_root(path: str) -> bool:
    return path == _norm(Path(path).anchor or os.sep)


def is_system_path(path) -> bool:
    p = _norm(path)
    return _is_disk_root(p) or any(_inside(p, d) for d in system_dirs())


def check_project(root, home=None) -> Optional[str]:
    """None if the folder is safe to give Codex, else why not."""
    p = _norm(root)
    if _is_disk_root(p):
        return f"{root} is the root of a disk. Codex could change anything on it, including the system."
    for d in system_dirs():
        if _inside(p, d):
            return f"{root} is inside a system folder ({d})."
        if _inside(d, p):
            return f"{root} contains a system folder ({d})."
    home = _norm(home or Path.home())
    if _inside(home, p):
        return (f"{root} is your whole home folder{' (or contains it)' if p != home else ''}. It holds your "
                "settings, keys and other programs' data.")
    return None


# Commands that can damage the system whatever folder they run in.
_DANGEROUS = [
    (r"\bformat(\.com)?\s+[a-z]:", "formats a disk"),
    (r"\b(format-volume|clear-disk|initialize-disk|remove-partition|resize-partition)\b", "changes a disk"),
    (r"\bdiskpart\b", "changes disks and partitions"),
    (r"\b(bcdedit|bootrec|bcdboot)\b", "changes how the computer starts"),
    (r"\breg(\.exe)?\s+(delete|add|import|load|unload|restore|copy)\b", "changes the Windows registry"),
    (r"\b(set-itemproperty|new-itemproperty|remove-itemproperty|remove-item|new-item)\b[^|;&]*\bhk(lm|cu|cr|u|cc):",
     "changes the Windows registry"),
    (r"\bvssadmin\s+(delete|resize)\b", "deletes system restore points"),
    (r"\bwbadmin\s+delete\b", "deletes system backups"),
    (r"\bcipher\s+/w\b", "wipes free disk space"),
    (r"\b(sfc|dism)\b[^|;&]*/(online|image|scannow)", "changes Windows system files"),
    (r"\bmkfs(\.\w+)?\b", "formats a disk"),
    (r"\b(fdisk|parted|sfdisk|wipefs)\b", "changes disks and partitions"),
    (r"\bdd\b[^|;&]*\bof=/dev/", "writes straight to a disk"),
    (r">\s*/dev/(sd|nvme|hd|disk)", "writes straight to a disk"),
    (r"\brm\s+(-\S+\s+)*(/|/\*|~|~/|~/\*|\$home|\$home/\*)(\s|$)", "deletes the whole disk or home folder"),
    (r"\b(chmod|chown)\s+(-\S+\s+)*\S+\s+/(\s|$)", "changes permissions on the whole disk"),
    (r"\b(shutdown|reboot|poweroff|halt|stop-computer|restart-computer)\b", "shuts down or restarts the computer"),
    (r"\b(del|erase|rd|rmdir)\s+(/\S+\s+)*[a-z]:\\\*?(\s|$)", "deletes the whole disk"),
]
# Commands that change files; with a system folder in them they are refused.
_CHANGES_FILES = re.compile(
    r"\b(del|erase|rd|rmdir|rm|remove-item|move|mv|move-item|ren|rename|rename-item|copy|cp|copy-item|xcopy|"
    r"robocopy|set-content|add-content|out-file|new-item|icacls|takeown|attrib|chmod|chown|ln|tee|sed\s+-i)\b|>",
    re.IGNORECASE)


def dangerous_command(command) -> Optional[str]:
    """Why the command would damage the system, or None."""
    if isinstance(command, (list, tuple)):
        command = " ".join(str(c) for c in command)
    text = str(command or "")
    for pattern, why in _DANGEROUS:
        if re.search(pattern, text, re.IGNORECASE):
            return why
    if _CHANGES_FILES.search(text):
        lowered = os.path.normcase(text) if os.name == "nt" else text
        for d in system_dirs():
            if d in lowered or d.replace("\\", "/") in lowered:
                return f"changes files in a system folder ({d})"
    return None


# --- secret files ----------------------------------------------------------------------------------
# Codex's sandbox controls writing, not reading, so these are kept away from Codex in three ways: Codex
# is told never to touch them, a request to leave the sandbox that touches one is refused, and the
# secret guard stops a turn the moment Codex starts to read or change one (see agent.py).

_SECRET_FILE = re.compile(
    r"^(\.env(\.(?!(example|sample|template|dist|defaults)$)[^.\\/]+)*"   # .env, .env.local (not .env.example)
    r"|id_(rsa|dsa|ecdsa|ed25519)|\.git-credentials|\.netrc|_netrc|\.pgpass|\.npmrc|\.pypirc"
    r"|credentials(\.json)?|secrets?\.(json|ya?ml|toml|env|txt)|service[-_]account.*\.json"
    r"|.*\.(pem|key|p12|pfx|kdbx|keystore|jks|sqlite|sqlite3|db))$",
    re.IGNORECASE)
_SECRET_FOLDERS = {"data", ".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker"}
_SKIP_FOLDERS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".tox", ".mypy_cache",
                 ".pytest_cache", ".next", "dist", "build", ".idea", ".vscode"}


def is_secret_path(path) -> bool:
    """Whether a path is a file that may hold passwords, keys or private data: .env files (not
    .env.example), keys, credentials, databases, or anything in a data/, .ssh/ or .aws/ folder."""
    parts = [p for p in re.split(r"[\\/]+", str(path or "").strip().strip("'\"`")) if p not in ("", ".")]
    if not parts:
        return False
    if any(p.lower() in _SECRET_FOLDERS for p in parts[:-1]):
        return True
    return bool(_SECRET_FILE.match(parts[-1]))


# Words after which a file name is being left out, not read: `Where-Object { $_ -ne '.env' }`,
# `rg --glob '!.env'`, `--exclude .env`. (Found in a real run: Codex leaving .env out set off the guard.)
_EXCLUDING = {"-ne", "-notlike", "-notmatch", "-notin", "-notcontains", "-exclude", "--exclude", "--exclude-dir",
              "--exclude-from", "-x", "-not", "!=", "-i", "--ignore", "--iglob", "-v", "--invert-match"}


# A pattern is text to look for, not a file to open: `-notmatch '^\.env$'`, `rg '\.env\b' src`,
# `Select-String -Pattern '.env'`. (Found in real runs: Codex searching for, or filtering out, the names of
# secret files set off the guard, and careful models do exactly that because they are told to avoid them.)
_PATTERN_OPERATOR = re.compile(r"(?i)(?<![\w-])-(?:[ci])?(?:not)?(?:match|like)\b|(?<![\w-])-(?:pattern|replace|split)\b"
                               r"|(?<![\w-])(?:-e|--regexp)(?=\s)")
_QUOTED = re.compile(r"""'[^']*'|"[^"]*\"""")
# Commands that read, copy or open what reaches them through a pipeline or loop.
_READS_CONTENT = re.compile(
    r"(?i)\b(get-content|gc|cat|type|more|less|head|tail|select-string|sls|copy-item|copy|cp|move-item|mv|"
    r"import-csv|import-clixml|convertfrom-json|invoke-item|ii|start-process|out-file|set-content|add-content|"
    r"compress-archive|foreach-object|%|xargs|openfile|readalltext|readalllines|readallbytes|\[io\.file\])\b|"
    r"\|\s*%")
_SEARCH_TOOLS = {"rg", "grep", "egrep", "fgrep", "ag", "ack", "findstr", "git-grep"}
_TAKES_VALUE = {"-g", "--glob", "--iglob", "-t", "--type", "-T", "--type-not", "-m", "--max-count", "-A", "-B", "-C",
                "--context", "--after-context", "--before-context", "-f", "--file", "--include", "--exclude",
                "--exclude-dir", "-d", "--max-depth", "--color", "--colour", "-M", "--max-columns", "-j", "--threads"}


def _unwrap(command: str) -> list:
    """The scripts a command runs: Codex reports `pwsh.exe -Command '<script>'` (or bash -lc) in shell quoting,
    which splits the script's own quotes apart. Each argument that is a script comes back on its own."""
    import shlex
    try:
        args = shlex.split(command, posix=True)
    except ValueError:
        return [command]
    return [a for a in args if " " in a.strip()] or [command]


def _blank_patterns(script: str) -> str:
    """The script with its search patterns blanked out, so only names it could open are left."""
    def blank(span):
        return " " * len(span)

    # a quoted (or single) argument right after a pattern operator: -match, -notmatch, -like, -pattern, -e ...
    # A positive filter (-match, -like) chooses files, so it only counts when nothing after it reads what it
    # chose: `Where-Object Name -match '\.env' | Get-Content` opens .env. -notmatch/-notlike leave files out.
    out = list(script)
    for m in _PATTERN_OPERATOR.finditer(script):
        positive = re.fullmatch(r"(?i)-[ci]?(match|like)", m.group(0))
        if positive and _READS_CONTENT.search(script, m.end()):
            continue
        rest = script[m.end():]
        stripped = len(rest) - len(rest.lstrip())
        q = _QUOTED.match(rest.lstrip()) or re.match(r"\S+", rest.lstrip())
        if q:
            start = m.end() + stripped
            out[start:start + len(q.group(0))] = blank(q.group(0))
    script = "".join(out)

    # a search tool's first argument that isn't an option: the pattern (rg PATTERN [PATH ...]). Commands are
    # split at ; | & outside quotes only: a pattern like 'a|\.env' has a | of its own.
    tokens = list(re.finditer(r"""(?:'[^']*'|"[^"]*"|[^\s'";|&])+|[;|&]+""", script))
    segments, current = [], []
    for t in tokens:
        if re.fullmatch(r"[;|&]+", t.group(0)):
            segments.append(current)
            current = []
        else:
            current.append(t)
    segments.append(current)
    for n, words in enumerate(segments):
        if words and any(_READS_CONTENT.search(" ".join(w.group(0) for w in later)) for later in segments[n + 1:]):
            continue  # `rg --files | rg '\.env$' | xargs cat`: what it finds is then read
        raw = [w.group(0).strip("'\"") for w in words]
        tools = [r.lower().replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe") for r in raw]
        tool = next((i for i, n in enumerate(tools) if n in _SEARCH_TOOLS or (n == "grep" and i and tools[i - 1] == "git")),
                    None)
        if tool is None or "-e" in raw or "--regexp" in raw:
            continue
        slash_options = tools[tool] == "findstr"  # findstr /s /i "text" files
        i = tool + 1
        while i < len(words):
            w = raw[i]
            if w in _TAKES_VALUE:
                i += 2
                continue
            if (w.startswith("-") and w != "-") or (slash_options and w.startswith("/")):
                i += 1
                continue
            start, end = words[i].span()
            script = script[:start] + blank(words[i].group(0)) + script[end:]
            break
    return script


def _looks_like_pattern(token: str) -> bool:
    """A regular expression rather than a file name: ^.env$, (?i).env, [^.]env. Backslash escapes don't count:
    Windows paths are full of them (certs\\server.pem), so they are never a reason to let a name through."""
    return bool(re.search(r"\(\?|\[\^|^\^.*\$$", token))


def _names_secret(token: str) -> bool:
    """True if the token is a secret file's name, or a wildcard or anchored pattern that matches one
    (.env*, *.pem, \\.env$)."""
    if is_secret_path(token):
        return True
    if not re.search(r"[*?$^]", token):
        return False
    plain = re.sub(r"^\^|\$$|^\\+(?=\.)", "", token)  # ^.env$ -> .env, \.env -> .env
    if is_secret_path(plain):
        return True
    import fnmatch
    name = re.split(r"[\\/]+", plain)[-1]
    samples = (".env", ".env.local", "id_rsa", "credentials.json", "secrets.json", "x.pem", "x.key", "x.p12",
               "x.pfx", "x.db", "x.sqlite", ".netrc", ".npmrc", ".git-credentials")
    return bool(name) and any(fnmatch.fnmatch(s, name) for s in samples) and name not in ("*", "*.*", "?*")


def secret_in_command(command) -> Optional[str]:
    """The first secret file a command names (and isn't leaving out or searching for), or None."""
    if isinstance(command, (list, tuple)):
        command = " ".join(str(c) for c in command)
    for script in _unwrap(str(command or "")):
        found = _secret_in_script(_blank_patterns(script))
        if found:
            return found
    return None


def _secret_in_script(script: str) -> Optional[str]:
    previous = ""
    for token in re.split(r"""[\s'"`|;&<>(),={}]+""", script):
        token = token.strip().rstrip(":")
        if not token:
            continue
        excluded = previous.lower() in _EXCLUDING or token.startswith(("!", ":!", ":(exclude)"))
        if not excluded and not token.startswith("-") and not _looks_like_pattern(token) and _names_secret(token):
            return token
        previous = token
    return None


def guard_item(item) -> Optional[str]:
    """For the secret guard: why Codex must be stopped before this command or edit, or None.
    `item` is a commandExecution or fileChange item as Codex reports it when it starts."""
    def value(x):
        return getattr(x, "value", x)

    kind = getattr(item, "type", "")
    if kind == "commandExecution":
        for action in getattr(item, "command_actions", None) or []:
            action = getattr(action, "root", action)
            path = getattr(action, "path", None)
            if value(getattr(action, "type", "")) == "read" and path and is_secret_path(path):
                return f"Codex started to read a secret file ({path})"
            found = secret_in_command(getattr(action, "command", ""))
            if found:
                return f"Codex started a command that touches a secret file ({found})"
        found = secret_in_command(getattr(item, "command", ""))
        if found:
            return f"Codex started a command that touches a secret file ({found})"
    elif kind == "fileChange":
        for change in getattr(item, "changes", None) or []:
            path = getattr(change, "path", "")
            if is_secret_path(path):
                return f"Codex started to change a secret file ({path})"
    return None


def find_secret_files(root, limit: int = 20, max_files: int = 50_000) -> list[str]:
    """Secret files inside the project (relative paths, at most `limit`), skipping folders like .git and
    node_modules. Used to warn at start: inside the project, only instructions and the guard keep Codex
    away from them."""
    root = Path(root)
    found, seen = [], 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_FOLDERS)
        for name in sorted(files):
            seen += 1
            rel = os.path.relpath(os.path.join(folder, name), root)
            if is_secret_path(rel):
                found.append(rel.replace("\\", "/"))
                if len(found) >= limit:
                    return found
            if seen >= max_files:
                return found
    return found


def running_interactively() -> bool:
    return sys.stdin is not None and sys.stdin.isatty()
