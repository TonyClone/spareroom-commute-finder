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
        "assets": [
            {"name": a.get("name") or "", "url": a.get("browser_download_url") or ""}
            for a in d.get("assets") or []
        ],
    }


def is_newer(tag: str) -> bool:
    return _parse_version(tag) > _parse_version(__version__)


# --- Startup update notice -------------------------------------------------
# The menu kicks off a background check when it opens; once (if) it finds a
# newer release, the home screen shows "press 9 to update". Threaded so a slow
# or offline network can never delay the launch, and any failure stays silent.

_background: dict = {"tag": None, "started": False}


def start_update_check(*, timeout: float = 6.0) -> None:
    """Begin a one-shot background check for a newer release (idempotent)."""
    if _background["started"]:
        return
    _background["started"] = True
    if is_git_checkout():
        return  # developers update with `git pull`; don't nag them

    import threading

    def _worker() -> None:
        try:
            info = check_latest(timeout=timeout)
            if info and info["tag"] and is_newer(info["tag"]):
                _background["tag"] = info["tag"]
        except Exception:  # noqa: BLE001 - a failed check must never surface
            pass

    threading.Thread(target=_worker, name="flatfinder-update-check", daemon=True).start()


def update_notice() -> str | None:
    """Newer release tag found by the background check, or None (yet)."""
    return _background["tag"]


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

    if getattr(sys, "frozen", False):
        # Standalone .exe/.app build: the code lives inside the executable, so
        # copying source files next to it does nothing. Fetch the new binary
        # from the release instead (we can't overwrite ourselves while running).
        return _update_frozen(info, progress, timeout=timeout)

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


# Release asset names per platform, newest naming first (older releases shipped
# the mac binary un-zipped; keep matching it so updates from them still work).
_FROZEN_ASSETS = {
    "win32": ["Flatfinder-windows.exe"],
    "darwin": ["Flatfinder-macos.zip", "Flatfinder-macos"],
}


def _update_frozen(info: dict, progress: Progress, *, timeout: float) -> bool:
    """Download the new standalone binary next to the running one.

    A running executable can't replace itself, so the new version lands beside
    it as e.g. ``Flatfinder-v0.2.0.exe`` — settings/data are found because they
    live in the same folder. The user is told to use the new file from now on.
    """
    candidates = _FROZEN_ASSETS.get(sys.platform, [])
    asset = next(
        (a for name in candidates for a in info.get("assets", []) if a["name"] == name and a["url"]),
        None,
    )
    if asset is None:
        progress(
            f"{info['tag']} is out, but no download for this platform was found on the "
            f"release. Grab it manually: {info['url']}"
        )
        return False

    exe = Path(sys.executable).resolve()
    suffix = ".exe" if sys.platform == "win32" else ""
    target = exe.parent / f"Flatfinder-{info['tag']}{suffix}"

    progress(f"Downloading {info['tag']} ({asset['name']}) …")
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(asset["url"])
            resp.raise_for_status()
            payload = resp.content
    except httpx.HTTPError as e:
        progress(f"Download failed ({e}). Nothing was changed.")
        return False

    try:
        if asset["name"].endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                names = [n for n in zf.namelist() if not n.endswith("/")]
                if not names:
                    progress("The downloaded archive was empty. Nothing changed.")
                    return False
                payload = zf.read(names[0])
        target.write_bytes(payload)
        if sys.platform != "win32":
            target.chmod(target.stat().st_mode | 0o755)
    except (zipfile.BadZipFile, OSError) as e:
        progress(f"Couldn't save the new version ({e}). Nothing was changed.")
        return False

    progress(
        f"Done! The new version is saved next to this one as:  {target.name}\n"
        f"Close this window and double-click {target.name} from now on (your settings and\n"
        f"seen-list carry over automatically — they live in this folder, not in the app).\n"
        f"You can delete the old {exe.name} once the new one runs. A Desktop shortcut, if\n"
        f"you made one, re-points itself to the new version the first time you run it."
    )
    return True
