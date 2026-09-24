"""Runtime defaults shared by every platform process."""

import os
import sys
import tempfile
from pathlib import Path


def configure_cache_dir() -> None:
    """Keep temporary exports, OCR files and import workspaces in one place."""
    configured = os.getenv("CACHE_DIR", "").strip()
    # A local checkout is normally run from the repository directory, so this
    # resolves to its parent workspace cache (D:\爬虫数据平台\cache here).
    cache_dir = Path(configured).expanduser() if configured else Path.cwd().parent / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(cache_dir)


configure_cache_dir()
sys.dont_write_bytecode = True
