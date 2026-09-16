import json

import pytest
from pathlib import Path

from igcleanup.settings import Settings, load_settings, save_settings


def test_load_creates_defaults_when_missing(tmp_path: Path):
    path = tmp_path / "settings.json"
    s = load_settings(path)
    assert s == Settings()
    assert path.exists()
    assert json.loads(path.read_text())["daily_action_cap"] == 100


def test_defaults_match_spec():
    s = Settings()
    assert s.daily_action_cap == 100
    assert s.inactivity_months == 12
    assert s.scan_delay_seconds == (3, 7)
    assert s.scan_rest_every == 50
    assert s.scan_rest_seconds == (120, 240)
    assert s.action_delay_seconds == (30, 90)


def test_round_trip(tmp_path: Path):
    path = tmp_path / "settings.json"
    s = Settings(daily_action_cap=42, inactivity_months=6)
    save_settings(path, s)
    assert load_settings(path) == s


def test_unknown_keys_ignored_and_missing_keys_defaulted(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"daily_action_cap": 5, "bogus": 1}))
    s = load_settings(path)
    assert s.daily_action_cap == 5
    assert s.inactivity_months == 12


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_settings(path) == Settings()
    assert json.loads(path.read_text())["daily_action_cap"] == 100


def test_speed_presets_apply_and_round_trip(tmp_path: Path):
    from igcleanup.settings import with_speed
    s = with_speed(Settings(), "fastest")
    assert s.speed == "fastest" and s.action_delay_seconds == (8, 20) and s.scan_rest_every == 80
    path = tmp_path / "settings.json"
    save_settings(path, s)
    assert load_settings(path) == s
    with pytest.raises(ValueError):
        with_speed(Settings(), "ludicrous")
