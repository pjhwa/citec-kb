"""scripts/cron_snapshot.py — schedule maths and status heuristics."""

from __future__ import annotations

import importlib.util
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "cron_snapshot", Path(__file__).resolve().parents[3] / "scripts" / "cron_snapshot.py"
)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

NOW = datetime(2026, 11, 5, 9, 0).astimezone()  # Thursday


def test_prev_expected_daily_and_step_and_weekday():
    assert cs.prev_expected(cs.parse_schedule("0 4 * * *"), NOW) == NOW.replace(hour=4, minute=0)
    assert cs.prev_expected(cs.parse_schedule("0 12 * * *"), NOW) == (NOW - timedelta(days=1)).replace(hour=12, minute=0)
    assert cs.prev_expected(cs.parse_schedule("*/5 * * * *"), NOW.replace(minute=7)) == NOW.replace(minute=5)
    # Mondays 03:00 → Nov 2
    assert cs.prev_expected(cs.parse_schedule("0 3 * * 1"), NOW) == NOW.replace(day=2, hour=3, minute=0)
    assert cs.parse_schedule("bad") is None


def _job(tmp, line, log_age_min=None, log_text="ok\n"):
    logs = tmp / "logs"
    logs.mkdir(exist_ok=True)
    lf = logs / "j.log"
    if log_age_min is not None:
        lf.write_text(log_text)
        t = time.time() - log_age_min * 60
        os.utime(lf, (t, t))
    snap = cs.build(line.replace("LOG", str(lf)), logs, datetime.now().astimezone())
    return snap["jobs"][0]


def test_status_ok_failed_missed_unknown(tmp_path):
    every_min = "* * * * * /bin/echo >> LOG 2>&1"
    assert _job(tmp_path, every_min, log_age_min=0)["status"] == "ok"
    assert _job(tmp_path, every_min, log_age_min=0, log_text="step\n❌ 실패\n")["status"] == "failed"
    daily = "0 * * * * /bin/echo >> LOG 2>&1"  # hourly, log untouched for 3h → missed
    assert _job(tmp_path, daily, log_age_min=180)["status"] == "missed"
    assert _job(tmp_path, "* * * * * /bin/echo", log_age_min=None)["status"] == "unknown"  # no redirect
    assert cs.parse_crontab("@reboot /bin/true")[0]["sched"] is None
    assert cs.parse_crontab("MAILTO=a@b\n# c\n") == []


def test_json_null_error_field_is_not_an_error():
    ok = '{"id":"e1","type":"decision_topic_scan","status":"queued","result":null,"error":null}'
    assert not cs.has_error(ok)
    assert not cs.has_error('{"error": null, "errors": 0}')
    assert cs.has_error('{"status":"failed","error":"boom"}')
    assert cs.has_error("ERROR: curl failed")
    assert cs.has_error("Traceback (most recent call last):")
