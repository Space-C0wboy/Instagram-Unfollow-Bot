"""Run the UI against a FakeDriver with a few scripted accounts. No Instagram involved."""
import asyncio
from datetime import date
from pathlib import Path

import uvicorn

from igcleanup.driver.types import Gone, NeverPosted, Posted
from igcleanup.jobs.engine import JobRunner
from igcleanup.jobs.progress import ProgressBus
from igcleanup.settings import Settings
from igcleanup.storage.db import connect
from igcleanup.ui.app import AppContext, create_app

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.fake_driver import FakeDriver  # noqa: E402

names = [f"user{i:02d}" for i in range(30)]
profiles = {}
for i, n in enumerate(names):
    profiles[n] = [Gone(), NeverPosted("") , Posted(date(2024, 1, 1), 4, ""), Posted(date(2026, 8, 1), 9, "")][i % 4]
# XSS regression check: a hostile display name must render as literal text, never execute.
profiles["user02"] = Posted(date(2024, 1, 1), 4, "", "<img src=x onerror=window.__pwned=1>")
driver = FakeDriver(following=[(n, n.title()) for n in names], profiles=profiles)
settings = Settings(scan_delay_seconds=(0.2, 0.4), action_delay_seconds=(0.2, 0.4), scan_rest_seconds=(0, 0))
ctx = AppContext(conn=connect(":memory:"), driver=driver, settings_path=Path("dev-settings.json"),
                 settings=settings, backups_dir=Path("dev-backups"), runner=JobRunner(), bus=ProgressBus())
ctx.login_ready.set()
uvicorn.run(create_app(ctx), host="127.0.0.1", port=8765)
