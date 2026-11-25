"""The secret guard must tell a command that opens a secret file from one that only searches for, or filters
out, its name. The first cases are rebuilt from two real false alarms; the rest must still be stopped."""

import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_app import protect  # noqa: E402

PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"


def as_codex_reports(script: str) -> str:
    """How Codex reports a PowerShell command: the arguments in shell quoting."""
    return shlex.join([PWSH, "-Command", script])


# What gpt-6.1-terra really ran (the benchmark's ledger task): listing files, leaving the secret ones out.
TERRA = ("Get-ChildItem -Force | Where-Object { $_.Name -notmatch '^\\.env($|\\.)|^\\.aws$|^data$' } | "
         "Select-Object Name,Mode,Length")
# What Codex really ran in the /pr fix check: searching the tests for secret-file names.
PR_FIX = ("rg -n -g 'test*.py' -g 'conftest.py' "
          "'CODEX_APP_TEST_REAL_SCHEDULER|\\.env\\b|credentials\\.json|\\.sqlite|\\.aws'")


@pytest.mark.parametrize("command", [
    as_codex_reports(TERRA),
    f'"{PWSH}" -Command "{PR_FIX}"',
    PR_FIX,
    "Get-ChildItem -Recurse | Where-Object { $_.Name -match '\\.env' } | Select-Object FullName",
    "Get-ChildItem | Where-Object Name -notlike '.env*'",
    "Select-String -Path src/*.py -Pattern '.env'",
    "grep -rn '\\.env' src",
    "grep -rn -e .env src",
    "rg -l --glob '*.py' credentials.json src",
    "git grep -n id_rsa -- docs",
    "findstr /s /i \".env\" *.md",
    "rg '^\\.env$' --files",
    "Get-ChildItem *.py | Select-Object Name",  # wildcards that can't match a secret file
    "rg TODO -g '*.md'",
    "ls *",
])
def test_searching_for_or_filtering_out_a_secret_name_is_allowed(command):
    assert protect.secret_in_command(command) is None


@pytest.mark.parametrize("command,found", [
    (as_codex_reports("Get-Content -LiteralPath .env"), ".env"),
    (as_codex_reports("Get-Content .env | Select-String KEY"), ".env"),
    ("Select-String -Path .env -Pattern 'KEY'", ".env"),
    ("rg API_KEY .env", ".env"),
    ("grep -n password config/.env.production", "config/.env.production"),
    ("rg -e KEY .env", ".env"),
    ("cat data/users.json", "data/users.json"),
    ("type .env", ".env"),
    ("Get-ChildItem | Where-Object { $_.Name -eq '.env' } | Get-Content", ".env"),
    (as_codex_reports("python -c \"print(open('.env').read())\""), ".env"),
    ('bash -lc "cat ~/.ssh/id_rsa"', "~/.ssh/id_rsa"),
    ("copy certs\\server.pem out.txt", "certs\\server.pem"),
    # trying to slip a read past the pattern rule: a filter that chooses the file, then reads it
    ("Get-ChildItem -Recurse | Where-Object Name -match '\\.env' | Get-Content", "\\.env"),
    ("Get-ChildItem | Where-Object { $_.Name -like '.env*' } | ForEach-Object { gc $_ }", ".env*"),
    ("Get-ChildItem | ? Name -match '.env' | Select-String KEY", ".env"),
    ("rg KEY -g '*.py' .env", ".env"),
    ("grep -f .env src/app.py", ".env"),
    ("Get-ChildItem -Recurse -Include .env | Select-String -Pattern KEY", ".env"),
    ("rg --files | rg '\\.env$' | xargs cat", "\\.env$"),  # find the secret files, then read them
    ("Get-Content *.pem", "*.pem"),
    ("cat .env*", ".env*"),
])
def test_opening_a_secret_file_is_still_stopped(command, found):
    assert protect.secret_in_command(command) == found


def test_the_guard_item_for_the_real_false_alarm():
    from types import SimpleNamespace as NS
    item = NS(type="commandExecution", command=as_codex_reports(TERRA),
              command_actions=[NS(root=NS(type="unknown", command=TERRA))])
    assert protect.guard_item(item) is None
