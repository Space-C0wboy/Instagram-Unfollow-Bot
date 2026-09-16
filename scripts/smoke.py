"""Manual driver smoke test. Usage:

  python scripts/smoke.py login
  python scripts/smoke.py following
  python scripts/smoke.py inspect <username>
  python scripts/smoke.py dump <username> <outfile.html>   # save page.content() as a fixture
  python scripts/smoke.py unfollow <username>
  python scripts/smoke.py follow <username>
"""
import asyncio
import os
import sys
from pathlib import Path

# Use the same bundled Chromium the app uses (see main.py).
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

from igcleanup.driver.playwright_driver import PlaywrightDriver  # noqa: E402

PROFILE = Path("browser-profile")


async def main(argv: list[str]) -> None:
    cmd, args = argv[0], argv[1:]
    d = PlaywrightDriver(PROFILE)
    await d.start()
    try:
        await d.ensure_logged_in()
        print("logged in")
        if cmd == "following":
            n = 0
            async for u, name in d.list_following():
                n += 1
                print(n, u, name)
        elif cmd == "inspect":
            print(await d.inspect_profile(args[0]))
        elif cmd == "dump":
            await d.page.goto(f"https://www.instagram.com/{args[0]}/")
            await d.page.wait_for_timeout(3000)
            Path(args[1]).write_text(await d.page.content(), encoding="utf-8")
            print("saved", args[1])
        elif cmd == "unfollow":
            await d.unfollow(args[0]); print("unfollowed")
        elif cmd == "follow":
            await d.follow(args[0]); print("followed")
    finally:
        await d.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["login"]))
