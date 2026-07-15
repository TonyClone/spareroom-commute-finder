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
        try:
            # webbrowser.open_new_tab is the portable approach
            webbrowser.open_new_tab(url)
            opened.append(url)
        except Exception as e:
            logger.warning("Failed to open %s: %s", url, e)
            # Windows fallback
            if sys.platform == "win32":
                try:
                    subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
                    opened.append(url)
                except Exception as e2:
                    logger.warning("Fallback open failed: %s", e2)
        if delay_seconds > 0 and i + 1 < max_tabs:
            time.sleep(delay_seconds)
    return opened
