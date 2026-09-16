import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    daily_action_cap: int = 100
    inactivity_months: int = 12
    scan_delay_seconds: tuple[float, float] = (3, 7)
    scan_rest_every: int = 50
    scan_rest_seconds: tuple[float, float] = (120, 240)
    action_delay_seconds: tuple[float, float] = (30, 90)
    welcome_seen: bool = False
    rescan_after_days: int = 30
    speed: str = "careful"


_TUPLE_FIELDS = {"scan_delay_seconds", "scan_rest_seconds", "action_delay_seconds"}

# How long the tool waits, per preset. "careful" is the default and the only one that is
# not accompanied by a warning in the UI.
SPEED_PRESETS = {
    "careful": {"scan_delay_seconds": (3, 7), "scan_rest_every": 50, "scan_rest_seconds": (120, 240),
                "action_delay_seconds": (30, 90)},
    "faster": {"scan_delay_seconds": (2, 4), "scan_rest_every": 60, "scan_rest_seconds": (60, 120),
               "action_delay_seconds": (15, 45)},
    "fastest": {"scan_delay_seconds": (1, 2), "scan_rest_every": 80, "scan_rest_seconds": (30, 60),
                "action_delay_seconds": (8, 20)},
}


def with_speed(settings: "Settings", speed: str) -> "Settings":
    """Return settings with the pacing fields set from a preset."""
    from dataclasses import replace
    if speed not in SPEED_PRESETS:
        raise ValueError(f"unknown speed {speed!r}")
    return replace(settings, speed=speed, **SPEED_PRESETS[speed])


def load_settings(path: Path) -> Settings:
    if not path.exists():
        save_settings(path, Settings())
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("settings must be an object")
    except (OSError, ValueError):
        # A hand-edited or damaged file must not stop the app: start over with defaults.
        save_settings(path, Settings())
        return Settings()
    known = {f.name for f in fields(Settings)}
    kwargs = {}
    for key, value in raw.items():
        if key not in known:
            continue
        kwargs[key] = tuple(value) if key in _TUPLE_FIELDS else value
    return Settings(**kwargs)


def save_settings(path: Path, settings: Settings) -> None:
    data = asdict(settings)
    for key in _TUPLE_FIELDS:
        data[key] = list(data[key])
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
