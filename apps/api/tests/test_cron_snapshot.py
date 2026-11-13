"""scripts/cron_snapshot.py — schedule maths and status heuristics."""

from __future__ import annotations

import importlib.util
import os
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


def _job(tmp, line, log_age_min=None, log_text="ok\n", now=None):
    # now를 고정하면 "매시 N분" 같은 스케줄의 missed 유예(10분) 판정이 실행 시각에 흔들리지 않는다.
    now = now or datetime.now().astimezone()
    logs = tmp / "logs"
    logs.mkdir(exist_ok=True)
    lf = logs / "j.log"
    if log_age_min is not None:
        lf.write_text(log_text)
        t = now.timestamp() - log_age_min * 60
        os.utime(lf, (t, t))
    snap = cs.build(line.replace("LOG", str(lf)), logs, now)
    return snap["jobs"][0]


def test_status_ok_failed_missed_unknown(tmp_path):
    every_min = "* * * * * /bin/echo >> LOG 2>&1"
    assert _job(tmp_path, every_min, log_age_min=0)["status"] == "ok"
    assert _job(tmp_path, every_min, log_age_min=0, log_text="step\n❌ 실패\n")["status"] == "failed"
    daily = "0 * * * * /bin/echo >> LOG 2>&1"  # hourly, log untouched for 3h → missed
    # 매시 0분 job: 유예(10분)가 지난 시각(12:30)으로 고정해 실행 시각과 무관하게 판정한다.
    fixed = datetime(2026, 11, 5, 12, 30).astimezone()
    assert _job(tmp_path, daily, log_age_min=180, now=fixed)["status"] == "missed"
    # 예정 시각 직후(12:05, 유예 안)에는 로그가 오래돼도 아직 missed가 아니다.
    just_after = datetime(2026, 11, 5, 12, 5).astimezone()
    assert _job(tmp_path, daily, log_age_min=180, now=just_after)["status"] != "missed"
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
