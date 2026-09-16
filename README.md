# Instagram Cleanup

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-lightgrey.svg)](#quick-start)
[![Version 1.0](https://img.shields.io/badge/version-1.0-green.svg)](https://github.com/Space-C0wboy/Instagram-Unfollow-Bot/releases/tag/v1.0)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-pytest-informational.svg)](#development)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-ffdd00.svg)](https://buymeacoffee.com/spacec0wboy)

A small Windows desktop app that finds the Instagram accounts you follow that have
**not posted in over a year**, shows you the list, and unfollows the ones you pick,
**slowly and with a backup**, so you can change your mind later. It is built for
someone who is not a power user: one window, a few big buttons, no terminal.

> [!IMPORTANT]
> **Unofficial project.** This tool is not affiliated with, endorsed by, or supported by
> Instagram or Meta. "Instagram" is a trademark of its owner. It works by driving a real
> browser exactly as a person would; there is no Instagram API involved.

> [!WARNING]
> **Automating your account is against Instagram's [Terms of Use](https://help.instagram.com/581066165581870).**
> Accounts that do this can be temporarily restricted or permanently disabled, and nothing
> here can guarantee that will not happen to yours. The app shows this warning on every
> launch and will not run until you accept the risk.
>
> What it does to keep that risk low:
> - **Goes slowly.** Random, human-like pauses between every action, longer breaks now and
>   then, and a hard **daily limit** (default 100 unfollows or re-follows per day).
> - **Never guesses.** Nothing is unfollowed that you did not tick, and an account it could
>   not read is shown to you rather than sorted into a bucket.
> - **Backs up first.** Every unfollow run writes a CSV of who it is about to unfollow, and
>   the app can follow them again from that file.
> - **Uses your own browser session.** You log in yourself, in a real Chromium window; the
>   app never sees or stores your password.

## Features

| | |
|---|---|
| **Scan** | Lists everyone you follow and visits each profile to read the date of the newest post (pinned posts are skipped). Resumable: close the app any time and it carries on next launch. |
| **Review** | Four groups with checkboxes, search, and check all / uncheck all: *Inactive for over a year* (ticked), *Has never posted* (unticked), *Account no longer exists* (ticked), *Couldn't check* (unticked). Click a username to open the real profile. |
| **Keep list** | A **Keep** button on any row moves it into a *Keeping* group. Kept accounts are never unfollowed and survive rescans. |
| **Unfollow** | Works through the ticked accounts at a safe pace and stops at the daily limit with "Done for today". Press *Continue* tomorrow. Pause, resume, or stop at any point. |
| **Backup and Restore** | Each run saves `backups/unfollow-backup-YYYY-MM-DD-HHMM.csv` (opens in Excel). *Settings, Restore from backup* lists them; pick one, tick who to follow again, and it re-follows at the same pace. |
| **Incremental rescans** | *Scan again* keeps existing results and only re-checks accounts scanned more than N days ago (default 30), so a rescan takes minutes, not hours. |
| **Speed presets** | Careful (default), Faster, Fastest. The real delays are shown, and anything but Careful comes with a warning. A change applies mid-run. |
| **Polish** | Four themes (Light, Dark, Neon, Phosphor), a results screen when a job finishes, Windows notifications when a run finishes or needs you, progress in the title bar, a one-time welcome page, and plain-English "?" help marks. |
| **Activity log** | `activity.log` beside the app records every profile checked, every unfollow and re-follow, and every start, pause, stop, and finish, with timestamps. |

## Quick start

1. Download `Instagram-Cleanup.zip` from the [latest release](https://github.com/Space-C0wboy/Instagram-Unfollow-Bot/releases/latest) and unzip it anywhere, for example your Desktop.
2. Open the folder and double-click **Instagram Cleanup.exe**.
   - If Windows SmartScreen says "Windows protected your PC", click **More info**, then
     **Run anyway**. It appears because the app is not code-signed.
3. Read the warning screen. Press **I understand the risk and agree** to continue.
4. Two windows open. Log in to Instagram in the browser window; you only do this once.
5. Press **Scan my following**. For a few thousand follows this takes most of a day at the
   Careful speed. You can close the app whenever you like and press **Resume** later.
6. Press **Review results**, untick anyone you want to keep (or press **Keep**), then
   **Unfollow selected**.
7. When it says **Done for today**, come back tomorrow and press **Continue unfollowing**.

Closing either window closes the whole app; whatever it was doing is paused and continues
next time. While it works, leave the Instagram window alone: the app is using it.

### Files it creates

Everything lives beside the exe. Deleting the folder is a complete uninstall.

| File or folder | What it is |
|---|---|
| `settings.json` | Your settings (also editable by hand) |
| `data.db` | Scan results, ticks, keep list, and the daily counter |
| `backups/` | One CSV per unfollow run; what Restore reads |
| `avatars/` | Cached profile pictures |
| `activity.log` | One line per action, with a timestamp |
| `browser-profile/` | The Instagram browser session, so you stay logged in |
| `ui-profile/` | The app window's browser profile |
| `igcleanup.log` | Technical log, only interesting if something breaks |

## Settings

Open with the gear button. Changes apply at the next action, even during a run.

| Setting | Default | Notes |
|---|---|---|
| Daily unfollow limit | 100 | Unfollows and re-follows count together. Does not affect scanning. Lower is safer. |
| Treat as inactive after this many months | 12 | Changing it re-sorts the Review screen without a rescan. |
| Re-check accounts scanned more than this many days ago | 30 | Controls how much *Scan again* re-does. |
| Speed | Careful | See the table below. |
| Theme | Light | Cycle with the Theme button in the header. |

Speed presets:

| Preset | Between profile checks | Rest | Between unfollows |
|---|---|---|---|
| **Careful** | 3 to 7 s | 2 to 4 min every ~50 profiles | 30 to 90 s |
| Faster | 2 to 4 s | 1 to 2 min every ~60 profiles | 15 to 45 s |
| Fastest | 1 to 2 s | 30 to 60 s every ~80 profiles | 8 to 20 s |

All delays get a random, long-tailed jitter and occasional longer breaks, and the rest
cadence is irregular, so the timing does not look mechanical. The exact ranges are the
`*_seconds` fields in `settings.json` if you want something else.

## How it works

- **Browser automation, no API.** [Playwright](https://playwright.dev) drives a bundled
  Chromium with a persistent profile. Every Instagram selector lives in one file,
  `src/igcleanup/driver/selectors.py`, so an Instagram redesign is a one-file fix.
- **Local state in SQLite.** Every step is written immediately, which is what makes scans and
  runs resumable across restarts.
- **The window is a local web page.** A FastAPI server on a random localhost port serves a
  single vanilla-JavaScript page, opened in an app-mode Chromium window with no address bar.
  Progress streams over server-sent events. Only same-origin requests are accepted.
- **Safety rails.** Refuses to run if a different Instagram account is logged in than the one
  it was set up for; refuses to start a second copy; pauses (rather than guessing) on rate
  limits, lost connections, expired logins, or unexpected page layouts, and resumes by itself
  once you have logged back in.

## Development

Windows, Python 3.13.

```powershell
git clone https://github.com/Space-C0wboy/Instagram-Unfollow-Bot
cd Instagram-Unfollow-Bot
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
$env:PLAYWRIGHT_BROWSERS_PATH = "0"      # the app looks for Chromium inside the package
.\.venv\Scripts\python -m playwright install chromium
```

```powershell
.\.venv\Scripts\python -m pytest         # unit tests; no network, Instagram is faked
.\.venv\Scripts\python -m igcleanup.main # run from source (data files land in the current folder)
.\.venv\Scripts\python scripts\dev_server.py   # the UI alone, against a fake driver, on :8765
.\build.ps1                              # tests, then PyInstaller, then dist\Instagram-Cleanup.zip
```

The only code that touches Instagram is `src/igcleanup/driver/playwright_driver.py`, and it
has no unit tests by design. After changing it or `selectors.py`, run the smoke script against
a throwaway account before handing out a build:

```powershell
.\.venv\Scripts\python scripts\smoke.py login
.\.venv\Scripts\python scripts\smoke.py following
.\.venv\Scripts\python scripts\smoke.py inspect <username>
.\.venv\Scripts\python scripts\smoke.py unfollow <username>   # then: follow <username>
```

`scripts\smoke.py dump <username> <file.html>` saves a real page for the parser tests in
`tests/fixtures/html/`; strip `<script>` blocks and any personal names before committing one.

Build notes: PyInstaller output goes to `%USERPROFILE%\igcleanup-build` because the bundled
browser's paths are too long for a deep checkout on Windows. The headless browser build is
excluded from the bundle on purpose. Unsigned PyInstaller executables are sometimes flagged by
antivirus heuristics; allow the file if that happens.

## Good to know

- Accounts where your follow request is still pending ("Requested") may not unfollow cleanly;
  they end up under *Couldn't check* for you to handle by hand.
- If the scan stops with a message about Instagram's layout, wait an hour and press Resume
  first; Instagram sometimes serves empty pages when it is rate-limiting. If it keeps
  happening, the selectors need updating.
- Windows Focus Assist silently swallows the notifications.

## License

[MIT](LICENSE)

## Support

Open an issue on the [GitHub repository](https://github.com/Space-C0wboy/Instagram-Unfollow-Bot/issues).
For questions about your Instagram account itself, contact Instagram.

If this saved you an afternoon, you can [buy me a coffee](https://buymeacoffee.com/spacec0wboy).
