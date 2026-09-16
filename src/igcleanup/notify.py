"""Desktop notifications. A no-op anywhere winotify is unavailable."""
import logging

logger = logging.getLogger(__name__)

APP_NAME = "Instagram Cleanup"


def desktop_notify(title: str, message: str) -> None:
    try:
        from winotify import Notification  # imported lazily: Windows-only, optional
    except ImportError:
        return
    try:
        Notification(app_id=APP_NAME, title=title, msg=message, duration="short").show()
    except Exception:  # noqa: BLE001 - a failed toast must never affect the job
        logger.exception("desktop notification failed")
