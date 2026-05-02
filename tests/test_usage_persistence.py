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
    assert sum(day["requests"] for day in current["by_day"].values()) == 2
    assert (tmp_path / "usage_history.json").exists()


def test_usage_persistence_returns_empty_months_and_flushes(tmp_path) -> None:
    persistence = UsagePersistence(str(tmp_path))

    assert persistence.get_month("2026-01") == {
        "month": "2026-01",
        "total_requests": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "by_model": {},
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
