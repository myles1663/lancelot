from __future__ import annotations

import json

from src.core import usage_persistence
from src.core.usage_persistence import UsagePersistence


def test_usage_persistence_loads_existing_history(tmp_path) -> None:
    path = tmp_path / "usage_history.json"
    path.write_text(
        json.dumps(
            {
                "current_month": "2026-05",
                "months": {
                    "2026-05": {
                        "total_requests": 1,
                        "total_tokens": 10,
                        "total_cost": 0.01,
                        "by_model": {},
                        "by_lane": {"flagship_fast": {"requests": 1}},
                        "by_day": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    persistence = UsagePersistence(str(tmp_path))

    assert persistence.get_current_month()["month"] == "2026-05"
    assert persistence.get_available_months() == ["2026-05"]


def test_usage_persistence_recorder_creates_month_model_and_day_buckets(tmp_path) -> None:
    persistence = UsagePersistence(str(tmp_path))

    persistence.record("gpt-test", tokens=100, cost=0.0123456)
    persistence.record("gpt-test", tokens=50, cost=0.001)

    current = persistence.get_current_month()
    assert current["total_requests"] == 2
    assert current["total_tokens"] == 150
    assert current["total_cost"] == 0.013346
    assert current["by_model"]["gpt-test"]["requests"] == 2
    assert current["by_lane"]["unclassified"]["requests"] == 2
    assert sum(day["requests"] for day in current["by_day"].values()) == 2
    assert (tmp_path / "usage_history.json").exists()


def test_usage_persistence_records_lane_buckets(tmp_path) -> None:
    persistence = UsagePersistence(str(tmp_path))

    persistence.record(
        "gpt-test",
        tokens=100,
        cost=0.01,
        lane="flagship_fast",
        success=False,
        elapsed_ms=25.0,
    )
    persistence.record("gpt-test", tokens=50, cost=0.005, lane="flagship_fast")

    lane = persistence.get_current_month()["by_lane"]["flagship_fast"]
    assert lane["requests"] == 2
    assert lane["successes"] == 1
    assert lane["failures"] == 1
    assert lane["total_tokens_est"] == 150
    assert lane["total_cost_est"] == 0.015
    assert lane["avg_elapsed_ms"] == 12.5


def test_usage_persistence_exposes_legacy_unclassified_lane_for_old_history(tmp_path) -> None:
    path = tmp_path / "usage_history.json"
    path.write_text(
        json.dumps(
            {
                "current_month": "2026-04",
                "months": {
                    "2026-04": {
                        "total_requests": 3,
                        "total_tokens": 1200,
                        "total_cost": 0.02,
                        "by_model": {},
                        "by_day": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    persistence = UsagePersistence(str(tmp_path))

    lane = persistence.get_current_month()["by_lane"]["legacy_unclassified"]
    assert lane["requests"] == 3
    assert lane["successes"] == 3
    assert lane["total_tokens_est"] == 1200
    assert lane["total_cost_est"] == 0.02


def test_usage_persistence_returns_empty_months_and_flushes(tmp_path) -> None:
    persistence = UsagePersistence(str(tmp_path))

    assert persistence.get_month("2026-01") == {
        "month": "2026-01",
        "total_requests": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "by_model": {},
        "by_lane": {},
        "by_day": {},
    }
    persistence.flush()
    assert json.loads((tmp_path / "usage_history.json").read_text(encoding="utf-8"))["months"] == {}


def test_usage_persistence_handles_bad_input_files_and_save_errors(tmp_path, monkeypatch) -> None:
    (tmp_path / "usage_history.json").write_text("{not-json", encoding="utf-8")
    persistence = UsagePersistence(str(tmp_path))
    assert persistence.get_available_months() == []

    monkeypatch.setattr(usage_persistence.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("no write")))
    persistence.record("gpt-test", tokens=1, cost=0.1)
