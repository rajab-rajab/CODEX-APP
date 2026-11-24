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


def running_interactively() -> bool:
    return sys.stdin is not None and sys.stdin.isatty()
