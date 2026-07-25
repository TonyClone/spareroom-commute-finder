from __future__ import annotations

import logging
import subprocess
import sys
import time
import webbrowser
from typing import Iterable

logger = logging.getLogger(__name__)


def open_tabs(
    urls: Iterable[str],
    *,
    delay_seconds: float = 0.35,
    max_tabs: int = 15,
) -> list[str]:
    """Open URLs in the default browser, staggered so Chrome doesn't choke."""
    opened: list[str] = []
    for i, url in enumerate(urls):
        if i >= max_tabs:
            break
        if not url:
            continue
        ok = False
        try:
            # webbrowser.open_new_tab is the portable approach. It signals
            # "no browser could be launched" (headless box, broken $BROWSER)
            # by RETURNING False, not by raising — treat that as a failure so
            # unopened rooms are never marked seen.
            ok = bool(webbrowser.open_new_tab(url))
        except Exception as e:
            logger.warning("Failed to open %s: %s", url, e)
        if not ok and sys.platform == "win32":
            # Windows fallback
            try:
                subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
                ok = True
            except Exception as e2:
                logger.warning("Fallback open failed: %s", e2)
        if ok:
            opened.append(url)
        else:
            logger.warning("No browser available to open %s", url)
        if delay_seconds > 0 and i + 1 < max_tabs:
            time.sleep(delay_seconds)
    return opened
