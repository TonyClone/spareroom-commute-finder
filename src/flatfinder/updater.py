"""Self-update: pull the latest GitHub release in place, without the user having
to visit GitHub or re-download anything by hand.

Two modes, chosen automatically:
  * git checkout (developers) → we DON'T touch files; we tell you to `git pull`,
    so your local edits are never clobbered.
  * plain download (the ZIP install non-technical users have) → download the
    latest release, copy the new code over the app folder, and refresh deps.

Either way the user's own files — .env, config.yaml, data/ (seen-DB) — are never
overwritten (they aren't in the release archive, and we skip them defensively).
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from flatfinder import __version__
from flatfinder.config import ROOT

# The public repo. Update if the project moves.
REPO = "TonyClone/spareroom-commute-finder"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"

# Top-level names we must never overwrite/delete during an update.
_PRESERVE_TOP = {".env", "config.yaml", "data", ".venv", ".git"}

Progress = Callable[[str], None]


def _parse_version(v: str) -> tuple[int, ...]:
    """'v0.2.1' / '0.2.1-beta' → (0, 2, 1). Lenient on purpose.

    Zero-padded to 3 segments so "0.2" == "0.2.0" — otherwise (0,2,0) > (0,2)
    and an equal release would look "newer" forever."""
    v = (v or "").strip().lstrip("vV").split("+")[0].split("-")[0]
    out = [int("".join(c for c in p if c.isdigit()) or 0) for p in v.split(".")]
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_git_checkout() -> bool:
    return (ROOT / ".git").exists()


def _run_git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - git may be absent; that's fine
        return ""


def build_label() -> tuple[str, bool]:
    """Return (human label, is_dev).

    - Installed / ZIP release  → ("v0.1.0", False)
    - git checkout on a clean release tag → ("v0.1.0", False)
    - git checkout ahead of the tag or with uncommitted edits → ("v0.1.0-3-gabc ✎", True)

    Lets the UI say plainly whether you're running a shipped release or unreleased
    dev code, so you never wonder which one you're looking at.
    """
    base = f"v{__version__}"
    if not is_git_checkout():
        return base, False
    desc = _run_git("describe", "--tags", "--always", "--dirty")
    if not desc:
        return f"{base}-dev", True
    dirty = desc.endswith("-dirty")
    core = desc[: -len("-dirty")] if dirty else desc
    ahead = "-g" in core  # e.g. v0.1.0-3-gabc123 → commits since the tag
    on_clean_tag = core.startswith("v") and not ahead and not dirty
    if on_clean_tag:
        return core, False
    return f"{core}{'*' if dirty else ''}", True  # trailing * = uncommitted edits


def check_latest(*, timeout: float = 10.0) -> dict | None:
    """Latest release info, or None if the repo has no releases yet.

    Raises httpx.HTTPError only on genuine network/HTTP failures.
    """
    r = httpx.get(
        LATEST_RELEASE_API,
        timeout=timeout,
        headers={"Accept": "application/vnd.github+json"},
        follow_redirects=True,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    try:
        d = r.json()
    except ValueError as e:
        # Captive portal / proxy returning HTML with a 200 — treat like any
        # other network failure so update()'s "never raises" promise holds.
        raise httpx.HTTPError(f"GitHub returned non-JSON: {e}") from e
    return {
        "tag": d.get("tag_name") or "",
        "zipball": d.get("zipball_url") or "",
        "url": d.get("html_url") or "",
        "notes": d.get("body") or "",
    }


def is_newer(tag: str) -> bool:
    return _parse_version(tag) > _parse_version(__version__)


def _copy_over(src: Path, dst: Path) -> int:
    copied = 0
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if rel.parts and rel.parts[0] in _PRESERVE_TOP:
            continue  # never touch the user's data/config
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied += 1
    return copied


def update(progress: Progress = print, *, timeout: float = 60.0) -> bool:
    """Update in place to the latest release. Returns True if something changed.

    Never raises: all failures are reported via ``progress`` and return False, so a
    flaky network can't crash the app.
    """
    if is_git_checkout():
        progress("This is a git checkout — update with:  git pull   (keeps your local changes safe)")
        return False

    try:
        info = check_latest()
    except httpx.HTTPError as e:
        progress(f"Couldn't reach GitHub to check for updates ({e}). Try again later.")
        return False

    if not info or not info["tag"]:
        progress("No published release found yet — nothing to update to.")
        return False
    if not is_newer(info["tag"]):
        progress(f"Already up to date (v{__version__}).")
        return False

    progress(f"Updating v{__version__} → {info['tag']} …")
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(info["zipball"])
            resp.raise_for_status()
            payload = resp.content
    except httpx.HTTPError as e:
        progress(f"Download failed ({e}). Nothing was changed.")
        return False

    tmp = Path(tempfile.mkdtemp(prefix="flatfinder-update-"))
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = zf.namelist()
            if not names:
                progress("The downloaded archive was empty. Nothing changed.")
                return False
            top = names[0].split("/")[0]  # GitHub wraps everything in one folder
            zf.extractall(tmp)
        copied = _copy_over(tmp / top, ROOT)
        progress(f"Applied {copied} files (your settings, keys and seen-list were left untouched).")
    except (zipfile.BadZipFile, OSError) as e:
        progress(f"Update failed while unpacking ({e}). Nothing critical changed.")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Refresh dependencies best-effort (editable install → code is already live).
    if not getattr(sys, "frozen", False):
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "-e", str(ROOT)],
                check=False,
                timeout=300,
            )
        except Exception as e:  # noqa: BLE001 - non-fatal
            progress(f"(Couldn't auto-refresh dependencies: {e} — usually fine.)")

    progress(f"Updated to {info['tag']}! Close and reopen Flatfinder to use it.")
    return True
