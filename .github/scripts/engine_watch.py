"""For the weekly engine check (.github/workflows/engine-watch.yml).

    python .github/scripts/engine_watch.py newest        prints the newest openai-codex on PyPI and
                                                         whether requirements.txt already accepts it
    python .github/scripts/engine_watch.py widen VERSION  rewrites requirements.txt so it accepts VERSION

The range is widened only up to the next minor version (0.205.3 -> "<0.206"), so every new minor
version gets its own weekly test before it is accepted."""

import json
import re
import sys
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "requirements.txt"
PACKAGE = "openai-codex"


def newest_release() -> Version:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{PACKAGE}/json", timeout=30) as response:
        releases = json.loads(response.read().decode("utf-8"))["releases"]
    usable = []
    for text, files in releases.items():
        try:
            version = Version(text)
        except ValueError:
            continue
        if not version.is_prerelease and files and not all(f.get("yanked") for f in files):
            usable.append(version)
    return max(usable)


def requirement_line(text: str) -> str:
    for line in text.splitlines():
        if re.match(rf"\s*{re.escape(PACKAGE)}\b", line, re.IGNORECASE):
            return line.split("#")[0].strip()
    raise SystemExit(f"{PACKAGE} is not in requirements.txt")


def widened(line: str, version: Version) -> str:
    """The requirement with its upper bound moved to just above `version`'s minor version."""
    spec = Requirement(line).specifier
    lower = next((s for s in spec if s.operator == ">="), None)
    upper = f"<{version.major}.{version.minor + 1}"
    return f"{PACKAGE}{lower},{upper}" if lower else f"{PACKAGE}{upper}"


def main(argv) -> int:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    line = requirement_line(text)
    if argv[1:2] == ["newest"]:
        version = newest_release()
        accepted = Requirement(line).specifier.contains(version)
        print(f"version={version}")
        print(f"accepted={'true' if accepted else 'false'}")
        print(f"range={line}")
        return 0
    if argv[1:2] == ["widen"] and len(argv) == 3:
        new = widened(line, Version(argv[2]))
        REQUIREMENTS.write_text(text.replace(line, new, 1), encoding="utf-8")
        print(f"range={new}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
