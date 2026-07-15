#!/usr/bin/env python
"""One-command release. Maintainer-only.

    python scripts/release.py 0.1.1

Bumps __version__, commits "Release vX.Y.Z", tags it, and pushes main + the tag —
which triggers the CI in .github/workflows/release.yml to build the Windows/macOS
binaries and publish the GitHub Release. That published release is what the in-app
updater ("Update" / `flatfinder update`) then offers to users.

Refuses to run on a dirty tree so a release only ever contains committed work.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "src" / "flatfinder" / "__init__.py"
ACTIONS_URL = "https://github.com/TonyClone/spareroom-commute-finder/actions"


def _run(*args: str) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=str(ROOT), check=True)


def _out(*args: str) -> str:
    return subprocess.run(
        args, cwd=str(ROOT), capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/release.py X.Y.Z")
    version = sys.argv[1].lstrip("vV").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"Version must look like 1.2.3 (got {version!r}).")
    tag = f"v{version}"

    # 1. Clean tree only — a release must be reproducible from committed code.
    dirty = _out("git", "status", "--porcelain")
    if dirty:
        sys.exit("Working tree is not clean — commit or stash first:\n" + dirty)

    # 2. Don't re-use an existing tag.
    if _out("git", "tag", "-l", tag):
        sys.exit(f"Tag {tag} already exists. Pick a new version.")

    # 3. Bump the single source of truth.
    text = INIT.read_text(encoding="utf-8")
    bumped = re.sub(
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{version}"',
        text,
    )
    if bumped == text:
        sys.exit("Could not find __version__ in src/flatfinder/__init__.py")
    INIT.write_text(bumped, encoding="utf-8")
    print(f"Set __version__ = {version}")

    # 4. Commit, tag, push. (Uses the repo's configured identity; no attribution.)
    _run("git", "add", str(INIT.relative_to(ROOT)))
    _run("git", "commit", "-m", f"Release {tag}")
    _run("git", "tag", tag)
    _run("git", "push", "origin", "main", "--tags")

    print(f"\nReleased {tag}. CI is now building the binaries:\n  {ACTIONS_URL}")
    print("When it finishes, users can get it via the app's Update option.")


if __name__ == "__main__":
    main()
