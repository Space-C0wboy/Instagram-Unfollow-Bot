import asyncio
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from igcleanup.driver.types import Driver
from igcleanup.jobs.engine import MSG_LOGIN, BaseJob, JobRunner, months_ago, seconds_per_item
from igcleanup.jobs.pacing import Pacer
from igcleanup.jobs.progress import ProgressBus
from igcleanup.jobs.restore import RestoreJob, prepare_restore
from igcleanup.jobs.scan import ScanJob
from igcleanup.jobs.unfollow import UnfollowJob, prepare_unfollow
from igcleanup.settings import SPEED_PRESETS, Settings, save_settings, with_speed
from igcleanup.storage.accounts import Account, AccountRepo
from igcleanup.storage.backup import list_backups, read_backup
from igcleanup.storage.jobs import EventRepo, Job, JobRepo
from igcleanup.storage.meta import MetaRepo

STATIC = Path(__file__).parent / "static"
STALE_AFTER = timedelta(days=30)
TERMS_URL = "https://help.instagram.com/581066165581870"
REVIEW_GROUPS = ("inactive", "never_posted", "gone", "error")


@dataclass
class AppContext:
    conn: object
    driver: Driver
    settings_path: Path
    settings: Settings
    backups_dir: Path
    runner: JobRunner
    bus: ProgressBus
    login_ready: asyncio.Event = field(default_factory=asyncio.Event)
    now: Callable[[], datetime] = datetime.now
    today: Callable[[], date] = date.today
    avatars_dir: Path | None = None
    notifier: Callable[[str, str], None] | None = None
    activity_log: Path | None = None
    on_quit: Callable[[], None] | None = None


def build_job(ctx: AppContext, job: Job) -> BaseJob:
    common = dict(conn=ctx.conn, driver=ctx.driver, settings=lambda: ctx.settings,
                  pacer=Pacer(lambda: ctx.settings), bus=ctx.bus, now=ctx.now, notifier=ctx.notifier,
                  activity_log=ctx.activity_log)
    if job.type == "scan":
        return ScanJob(job.id, today=ctx.today, avatars_dir=ctx.avatars_dir,
                       refresh=(job.source == "refresh"), **common)
    if job.type == "unfollow":
        return UnfollowJob(job.id, today=ctx.today, **common)
    if job.type == "restore":
        return RestoreJob(job.id, backup_path=Path(job.source), today=ctx.today, **common)
    raise ValueError(f"Unknown job type {job.type}")


def _job_dict(job: Job | None) -> dict | None:
    if job is None:
        return None
    return {"id": job.id, "type": job.type, "state": job.state, "total": job.total,
            "done": job.done, "message": job.message}


def _account_dict(a: Account) -> dict:
    return {"username": a.username, "display_name": a.display_name, "profile_url": a.profile_url,
            "profile_pic_url": a.profile_pic_url,
            "last_post_date": a.last_post_date.isoformat() if a.last_post_date else None,
            "status": a.status, "selected": a.selected,
            "checked_at": a.checked_at.date().isoformat() if a.checked_at else None}


def _is_stale(ctx: AppContext, latest_scan: Job | None) -> bool:
    if not latest_scan or latest_scan.state != "done" or not latest_scan.finished_at:
        return False
    return latest_scan.finished_at.date() + STALE_AFTER < ctx.today()


async def watch_login(ctx: AppContext, interval: float = 5.0) -> None:
    jobs = JobRepo(ctx.conn)
    while True:
        await asyncio.sleep(interval)
        try:
            if ctx.runner.is_running():
                continue
            active = jobs.active()
            if active and active.state == "paused" and active.message == MSG_LOGIN:
                if await ctx.driver.is_logged_in():
                    ctx.login_ready.set()
                    ctx.runner.start(build_job(ctx, active))
        except Exception:
            logging.getLogger(__name__).exception("watch_login iteration failed")


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    if ctx.avatars_dir is not None:
        ctx.avatars_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/avatars", StaticFiles(directory=str(ctx.avatars_dir)), name="avatars")
    jobs = JobRepo(ctx.conn)
    accounts = AccountRepo(ctx.conn)
    event_repo = EventRepo(ctx.conn)

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        if request.method != "GET":
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    def busy() -> JSONResponse:
        return JSONResponse({"error": "busy"}, status_code=409)

    def paused_job() -> JSONResponse | None:
        active = jobs.active()
        if active is not None and active.state == "paused":
            return JSONResponse({"error": "job_paused"}, status_code=409)
        return None

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    async def state():
        latest_scan = jobs.latest("scan")
        active_job = _job_dict(jobs.active())
        if active_job is not None:
            active_job["actions_today"] = event_repo.count_on(ctx.now().date())
            active_job["daily_action_cap"] = ctx.settings.daily_action_cap
            active_job["seconds_per_item"] = seconds_per_item(ctx.settings, active_job["type"])
        return {
            "logged_in": ctx.login_ready.is_set(),
            "active_job": active_job,
            "latest_scan": _job_dict(latest_scan),
            "last_failed": _job_dict(jobs.latest_failed()),
            "account_count": accounts.count(),
            "targets_remaining": len(accounts.targets()),
            "unchecked": accounts.unchecked_count(),
            "stale": _is_stale(ctx, latest_scan),
            "settings": {"daily_action_cap": ctx.settings.daily_action_cap,
                         "inactivity_months": ctx.settings.inactivity_months,
                         "welcome_seen": ctx.settings.welcome_seen,
                         "rescan_after_days": ctx.settings.rescan_after_days,
                         "speed": ctx.settings.speed},
        }

    @app.post("/api/scan/start")
    async def scan_start(request: Request):
        body = await request.json()
        if ctx.runner.is_running():
            return busy()
        if not ctx.login_ready.is_set():
            return JSONResponse({"error": "not_logged_in"}, status_code=409)
        active = jobs.active()
        rescan = bool(body.get("rescan"))
        if active is not None and active.type != "scan" and active.state == "paused" and not rescan:
            return JSONResponse({"error": "job_paused"}, status_code=409)
        if rescan or active is None or active.type != "scan":
            if active is not None:
                jobs.update(active.id, state="failed", message="Replaced by a new scan",
                            finished_at=ctx.now())
            active = jobs.create("scan", total=accounts.count(), started_at=ctx.now(),
                                 source="refresh" if rescan else None)
        ctx.runner.start(build_job(ctx, active))
        return {"job_id": active.id}

    @app.post("/api/jobs/pause")
    async def pause():
        ctx.runner.request_pause()
        return {"ok": True}

    @app.post("/api/jobs/stop")
    async def stop_job():
        """Abandon a paused run. Ticks are left as they are, so the user can start again later."""
        if ctx.runner.is_running():
            return busy()
        active = jobs.active()
        if active is None or active.state != "paused":
            return JSONResponse({"error": "nothing_to_stop"}, status_code=404)
        # A stopped unfollow/restore is simply finished early; a stopped scan is incomplete,
        # so it must not masquerade as a finished scan with results to review.
        state = "failed" if active.type == "scan" else "done"
        jobs.update(active.id, state=state, message="Stopped early.", finished_at=ctx.now())
        return {"ok": True}

    @app.post("/api/jobs/resume")
    async def resume():
        if ctx.runner.is_running():
            return busy()
        active = jobs.active()
        if active is None or active.state != "paused":
            return JSONResponse({"error": "nothing_to_resume"}, status_code=404)
        if not ctx.login_ready.is_set():
            return JSONResponse({"error": "not_logged_in"}, status_code=409)
        ctx.runner.start(build_job(ctx, active))
        return {"job_id": active.id}

    @app.get("/api/review")
    async def review():
        accounts.rebucket(months_ago(ctx.today(), ctx.settings.inactivity_months))
        groups = {g: [] for g in REVIEW_GROUPS}
        groups["kept"] = []
        kept = accounts.kept_usernames()
        for a in accounts.all():
            if a.unfollowed_at is not None:
                continue
            if a.username in kept:
                groups["kept"].append(_account_dict(a))
            elif a.status in groups:
                groups[a.status].append(_account_dict(a))
        return {"groups": groups, "stale": _is_stale(ctx, jobs.latest("scan")),
                "unchecked": accounts.unchecked_count()}

    @app.post("/api/keep")
    async def keep(request: Request):
        body = await request.json()
        username, kept = body.get("username"), body.get("kept")
        if not isinstance(username, str) or not isinstance(kept, bool):
            return JSONResponse({"error": "invalid"}, status_code=422)
        if kept:
            accounts.keep(username, ctx.now())
        else:
            accounts.unkeep(username)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/summary")
    async def job_summary(job_id: int):
        try:
            job = jobs.get(job_id)
        except Exception:  # noqa: BLE001 - unknown id
            return JSONResponse({"error": "not_found"}, status_code=404)
        out = _job_dict(job)
        out["counts"] = accounts.status_counts() if job.type == "scan" else None
        out["remaining"] = len(accounts.targets()) if job.type == "unfollow" else max(0, job.total - job.done)
        return out

    @app.post("/api/welcome/seen")
    async def welcome_seen():
        ctx.settings = replace(ctx.settings, welcome_seen=True)
        save_settings(ctx.settings_path, ctx.settings)
        return {"ok": True}

    @app.post("/api/selection")
    async def selection(request: Request):
        body = await request.json()
        usernames = body.get("usernames")
        selected = body.get("selected")
        if not (isinstance(usernames, list) and all(isinstance(u, str) for u in usernames)
                and isinstance(selected, bool)):
            return JSONResponse({"error": "invalid"}, status_code=422)
        accounts.set_selected(usernames, selected)
        return {"ok": True}

    @app.post("/api/unfollow/start")
    async def unfollow_start():
        if ctx.runner.is_running():
            return busy()
        if (refused := paused_job()) is not None:
            return refused
        if not ctx.login_ready.is_set():
            return JSONResponse({"error": "not_logged_in"}, status_code=409)
        try:
            job, path = prepare_unfollow(ctx.conn, ctx.backups_dir, ctx.now)
        except ValueError:
            return JSONResponse({"error": "nothing_selected"}, status_code=400)
        ctx.runner.start(build_job(ctx, job))
        return {"job_id": job.id, "backup": str(path), "total": job.total}

    @app.get("/api/settings")
    async def get_settings():
        return {"daily_action_cap": ctx.settings.daily_action_cap,
                "inactivity_months": ctx.settings.inactivity_months,
                "rescan_after_days": ctx.settings.rescan_after_days,
                "speed": ctx.settings.speed,
                "speed_presets": SPEED_PRESETS}

    @app.put("/api/settings")
    async def put_settings(request: Request):
        body = await request.json()
        cap, months = body.get("daily_action_cap"), body.get("inactivity_months")
        days = body.get("rescan_after_days", ctx.settings.rescan_after_days)
        ok = all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in (cap, months, days))
        if not ok:
            return JSONResponse({"error": "invalid"}, status_code=422)
        speed = body.get("speed", ctx.settings.speed)
        if speed not in SPEED_PRESETS:
            return JSONResponse({"error": "invalid"}, status_code=422)
        ctx.settings = replace(ctx.settings, daily_action_cap=cap, inactivity_months=months,
                               rescan_after_days=days)
        if speed != ctx.settings.speed:
            ctx.settings = with_speed(ctx.settings, speed)
        save_settings(ctx.settings_path, ctx.settings)
        return await get_settings()

    @app.post("/api/quit")
    async def quit_app():
        """Declined the terms, or otherwise asked to close: shut the whole app down."""
        if ctx.on_quit is not None:
            ctx.on_quit()
        return {"ok": True}

    @app.post("/api/open_terms")
    async def open_terms():
        await ctx.driver.open_url(TERMS_URL)
        return {"ok": True}

    @app.post("/api/reset")
    async def reset_everything():
        """Start over: wipe results and the keep list, forget the account. Backups stay."""
        if ctx.runner.is_running():
            return busy()
        active = jobs.active()
        if active is not None:
            jobs.update(active.id, state="failed", message="Stopped by Start over.", finished_at=ctx.now())
        accounts.reset_all()
        MetaRepo(ctx.conn).delete("owner")
        MetaRepo(ctx.conn).delete("scan_warning")
        return {"ok": True}

    @app.get("/api/backups")
    async def backups():
        return [{"name": p.name, "count": len(read_backup(p))} for p in list_backups(ctx.backups_dir)]

    def backup_path(name: str) -> Path | None:
        path = ctx.backups_dir / str(name)
        if path.parent != ctx.backups_dir or not path.is_file():
            return None
        return path

    @app.get("/api/backups/{name}")
    async def backup_rows(name: str):
        path = backup_path(name)
        if path is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return {"name": path.name, "rows": [
            {"username": r.username, "profile_url": r.profile_url, "display_name": r.display_name,
             "last_post_date": r.last_post_date, "status": r.status}
            for r in read_backup(path)]}

    @app.post("/api/restore/start")
    async def restore_start(request: Request):
        body = await request.json()
        path = backup_path(str(body.get("name", "")))
        if path is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        usernames = body.get("usernames")
        if usernames is not None and not (isinstance(usernames, list)
                                          and all(isinstance(x, str) for x in usernames)):
            return JSONResponse({"error": "invalid"}, status_code=422)
        if ctx.runner.is_running():
            return busy()
        if (refused := paused_job()) is not None:
            return refused
        if not ctx.login_ready.is_set():
            return JSONResponse({"error": "not_logged_in"}, status_code=409)
        try:
            job = prepare_restore(ctx.conn, path, ctx.now, usernames)
        except ValueError:
            return JSONResponse({"error": "nothing_selected"}, status_code=400)
        ctx.runner.start(build_job(ctx, job))
        return {"job_id": job.id, "total": job.total}

    @app.post("/api/open/{username}")
    async def open_profile(username: str):
        await ctx.driver.open_profile(username)
        return {"ok": True}

    @app.get("/api/events")
    async def events(limit: int | None = None):
        # `limit` ends the stream after that many events. The page never passes it;
        # tests do, because the in-process test client waits for a response to finish.
        async def gen():
            q = ctx.bus.subscribe()
            sent = 0
            try:
                if ctx.bus.last is not None:
                    # Flagged so a reloaded page can tell a replay from a live change.
                    yield f"data: {json.dumps({**ctx.bus.last, 'replay': True})}\n\n"
                    sent += 1
                while limit is None or sent < limit:
                    event = await q.get()
                    yield f"data: {json.dumps(event)}\n\n"
                    sent += 1
            finally:
                ctx.bus.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    return app
