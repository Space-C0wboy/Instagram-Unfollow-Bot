import asyncio
import contextlib
import logging
import os
import socket
import sys
import webbrowser
from pathlib import Path

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

import uvicorn  # noqa: E402

from igcleanup.driver.playwright_driver import PlaywrightDriver  # noqa: E402
from igcleanup.driver.types import DriverError  # noqa: E402
from igcleanup.jobs.engine import JobRunner  # noqa: E402
from igcleanup.notify import desktop_notify  # noqa: E402
from igcleanup.jobs.progress import ProgressBus  # noqa: E402
from igcleanup.settings import load_settings  # noqa: E402
from igcleanup.storage.db import connect  # noqa: E402
from igcleanup.storage.jobs import JobRepo  # noqa: E402
from igcleanup.ui.app import AppContext, create_app, watch_login  # noqa: E402

logger = logging.getLogger(__name__)


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


INSTANCE_PORT = 47612  # bound for the life of the process: a second copy cannot bind it


def single_instance() -> socket.socket | None:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def already_running_message() -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, "Instagram Cleanup is already running. Look for its window.",
                                        "Instagram Cleanup", 0x40)
    except Exception:  # noqa: BLE001 - not Windows, or no desktop: fall back to text
        print("Instagram Cleanup is already running.")


def screen_size() -> tuple[int, int]:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001
        return 1920, 1080


def window_layout() -> dict:
    """Instagram on the left, the tool on the right; both shrink and overlap on small screens."""
    w, h = screen_size()
    ig_w, ig_h = min(1000, w - 40), min(850, h - 120)
    ui_w, ui_h = min(900, w - 40), min(1000, h - 80)
    if w >= ig_w + ui_w + 60:
        ui_x = 20 + ig_w + 20
    else:
        ui_x = max(0, w - ui_w - 20)
    return {"ig_size": (ig_w, ig_h), "ig_pos": (20, 40), "ui_size": (ui_w, ui_h), "ui_pos": (ui_x, 20)}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def open_ui_window(driver: PlaywrightDriver, base: Path, url: str, layout: dict):
    """Returns the UI browser context, or None if we fell back to the default browser."""
    try:
        ctx = await driver.playwright.chromium.launch_persistent_context(
            str(base / "ui-profile"), headless=False, no_viewport=True,
            # Sit to the right of the Instagram window instead of on top of it.
            args=[f"--app={url}", "--window-size={},{}".format(*layout["ui_size"]),
                  "--window-position={},{}".format(*layout["ui_pos"])],
            ignore_default_args=["--enable-automation"],
        )
        return ctx
    except Exception as exc:  # noqa: BLE001 - any failure here means "use the normal browser"
        logger.warning("App window failed, falling back to browser: %s", exc)
        webbrowser.open(url)
        return None


async def run(base: Path) -> None:
    settings = load_settings(base / "settings.json")
    conn = connect(base / "data.db")
    JobRepo(conn).pause_stale_running()
    layout = window_layout()
    driver = PlaywrightDriver(base / "browser-profile", viewport=layout["ig_size"], position=layout["ig_pos"])
    await driver.start()
    try:
        ctx = AppContext(conn=conn, driver=driver, settings_path=base / "settings.json",
                         settings=settings, backups_dir=base / "backups",
                         runner=JobRunner(), bus=ProgressBus(), avatars_dir=base / "avatars",
                         notifier=desktop_notify, activity_log=base / "activity.log")

        closed = asyncio.Event()
        ctx.on_quit = closed.set  # the terms screen's Decline button ends the app
        driver.context.on("close", lambda *_: closed.set())

        port = free_port()
        server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=port, log_level="warning",
                                                log_config=None, timeout_graceful_shutdown=3))

        async def _serve_guarded() -> None:
            # uvicorn calls sys.exit() internally on a bind failure; a bare SystemExit
            # escaping this task would crash the whole event loop before our shutdown
            # code ever runs, leaking the already-started driver. Convert it to a
            # regular exception so server_task completes normally instead.
            try:
                await server.serve()
            except SystemExit as exc:
                raise RuntimeError(f"uvicorn failed to start: {exc}") from exc

        server_task = asyncio.create_task(_serve_guarded())
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 10
        try:
            while not server.started:
                if server_task.done():
                    server_task.result()
                    break
                if loop.time() >= deadline:
                    raise RuntimeError("Server did not start")
                await asyncio.sleep(0.05)
        except Exception:
            server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server_task, timeout=5)
            raise

        url = f"http://127.0.0.1:{port}/"
        ui_ctx = await open_ui_window(driver, base, url, layout)
        if ui_ctx is not None:
            ui_ctx.on("close", lambda *_: closed.set())
        else:
            print(f"Instagram Cleanup is running at {url}. Close this window to quit.")

        async def login_then_ready():
            # Instagram in front during login; the tool window once logged in.
            with contextlib.suppress(Exception):
                await driver.bring_to_front()
            while not ctx.login_ready.is_set():
                try:
                    await driver.ensure_logged_in()
                except DriverError:
                    await asyncio.sleep(5)
                    continue
                ctx.login_ready.set()
            if ui_ctx is not None and ui_ctx.pages:
                with contextlib.suppress(Exception):
                    await ui_ctx.pages[0].bring_to_front()

        tasks = [asyncio.create_task(login_then_ready()), asyncio.create_task(watch_login(ctx))]

        try:
            await closed.wait()
        finally:
            ctx.runner.request_pause()
            try:
                await asyncio.wait_for(ctx.runner.wait(), timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            for t in tasks:
                t.cancel()
            server.should_exit = True
            await server_task
    finally:
        await driver.close()


def main() -> None:
    guard = single_instance()
    if guard is None:
        already_running_message()
        return
    if sys.stdout is None or sys.stderr is None:
        # Windowed PyInstaller build: there is no console, so these are None.
        # Redirect them to a log file so nothing (our own logging, or a
        # library that assumes a real stream) crashes trying to use them.
        log_file = open(base_dir() / "igcleanup.log", "a", encoding="utf-8")
        sys.stdout = log_file
        sys.stderr = log_file
        logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                             format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(base_dir()))


if __name__ == "__main__":
    main()
