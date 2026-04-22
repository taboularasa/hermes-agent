"""Tests for cron/jobs.py — schedule parsing, job CRUD, and due-job detection."""

import json
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cron.jobs import (
    parse_duration,
    parse_schedule,
    compute_next_run,
    create_job,
    load_jobs,
    save_jobs,
    get_job,
    list_jobs,
    update_job,
    pause_job,
    resume_job,
    remove_job,
    mark_job_run,
    advance_next_run,
    get_due_jobs,
    inspect_job_topology,
    inspect_persistence_ratchet,
    inspect_first_proof_point,
    inspect_trust_contract,
    save_job_output,
)


# =========================================================================
# parse_duration
# =========================================================================

class TestParseDuration:
    def test_minutes(self):
        assert parse_duration("30m") == 30
        assert parse_duration("1min") == 1
        assert parse_duration("5mins") == 5
        assert parse_duration("10minute") == 10
        assert parse_duration("120minutes") == 120

    def test_hours(self):
        assert parse_duration("2h") == 120
        assert parse_duration("1hr") == 60
        assert parse_duration("3hrs") == 180
        assert parse_duration("1hour") == 60
        assert parse_duration("24hours") == 1440

    def test_days(self):
        assert parse_duration("1d") == 1440
        assert parse_duration("7day") == 7 * 1440
        assert parse_duration("2days") == 2 * 1440

    def test_whitespace_tolerance(self):
        assert parse_duration("  30m  ") == 30
        assert parse_duration("2 h") == 120

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_duration("abc")
        with pytest.raises(ValueError):
            parse_duration("30x")
        with pytest.raises(ValueError):
            parse_duration("")
        with pytest.raises(ValueError):
            parse_duration("m30")


# =========================================================================
# parse_schedule
# =========================================================================

class TestParseSchedule:
    def test_duration_becomes_once(self):
        result = parse_schedule("30m")
        assert result["kind"] == "once"
        assert "run_at" in result
        # run_at should be a valid ISO timestamp string ~30 minutes from now
        run_at_str = result["run_at"]
        assert isinstance(run_at_str, str)
        run_at = datetime.fromisoformat(run_at_str)
        now = datetime.now().astimezone()
        assert run_at > now
        assert run_at < now + timedelta(minutes=31)

    def test_every_becomes_interval(self):
        result = parse_schedule("every 2h")
        assert result["kind"] == "interval"
        assert result["minutes"] == 120

    def test_every_case_insensitive(self):
        result = parse_schedule("Every 30m")
        assert result["kind"] == "interval"
        assert result["minutes"] == 30

    def test_cron_expression(self):
        pytest.importorskip("croniter")
        result = parse_schedule("0 9 * * *")
        assert result["kind"] == "cron"
        assert result["expr"] == "0 9 * * *"

    def test_iso_timestamp(self):
        result = parse_schedule("2030-01-15T14:00:00")
        assert result["kind"] == "once"
        assert "2030-01-15" in result["run_at"]

    def test_invalid_schedule_raises(self):
        with pytest.raises(ValueError):
            parse_schedule("not_a_schedule")

    def test_invalid_cron_raises(self):
        pytest.importorskip("croniter")
        with pytest.raises(ValueError):
            parse_schedule("99 99 99 99 99")


# =========================================================================
# compute_next_run
# =========================================================================

class TestComputeNextRun:
    def test_once_future_returns_time(self):
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        schedule = {"kind": "once", "run_at": future}
        assert compute_next_run(schedule) == future

    def test_once_recent_past_within_grace_returns_time(self, monkeypatch):
        now = datetime(2026, 3, 18, 4, 22, 3, tzinfo=timezone.utc)
        run_at = "2026-03-18T04:22:00+00:00"
        monkeypatch.setattr("cron.jobs._hermes_now", lambda: now)

        schedule = {"kind": "once", "run_at": run_at}

        assert compute_next_run(schedule) == run_at

    def test_once_past_returns_none(self):
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        schedule = {"kind": "once", "run_at": past}
        assert compute_next_run(schedule) is None

    def test_once_with_last_run_returns_none_even_within_grace(self, monkeypatch):
        now = datetime(2026, 3, 18, 4, 22, 3, tzinfo=timezone.utc)
        run_at = "2026-03-18T04:22:00+00:00"
        monkeypatch.setattr("cron.jobs._hermes_now", lambda: now)

        schedule = {"kind": "once", "run_at": run_at}

        assert compute_next_run(schedule, last_run_at=now.isoformat()) is None

    def test_interval_first_run(self):
        schedule = {"kind": "interval", "minutes": 60}
        result = compute_next_run(schedule)
        next_dt = datetime.fromisoformat(result)
        # Should be ~60 minutes from now
        assert next_dt > datetime.now().astimezone() + timedelta(minutes=59)

    def test_interval_subsequent_run(self):
        schedule = {"kind": "interval", "minutes": 30}
        last = datetime.now().astimezone().isoformat()
        result = compute_next_run(schedule, last_run_at=last)
        next_dt = datetime.fromisoformat(result)
        # Should be ~30 minutes from last run
        assert next_dt > datetime.now().astimezone() + timedelta(minutes=29)

    def test_cron_returns_future(self):
        pytest.importorskip("croniter")
        schedule = {"kind": "cron", "expr": "* * * * *"}  # every minute
        result = compute_next_run(schedule)
        assert isinstance(result, str), f"Expected ISO timestamp string, got {type(result)}"
        assert len(result) > 0
        next_dt = datetime.fromisoformat(result)
        assert isinstance(next_dt, datetime)
        assert next_dt > datetime.now().astimezone()

    def test_unknown_kind_returns_none(self):
        assert compute_next_run({"kind": "unknown"}) is None


# =========================================================================
# Job CRUD (with tmp file storage)
# =========================================================================

@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    """Redirect cron storage to a temp directory."""
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    return tmp_path


class TestJobCRUD:
    def test_create_and_get(self, tmp_cron_dir):
        job = create_job(prompt="Check server status", schedule="30m")
        assert job["id"]
        assert job["prompt"] == "Check server status"
        assert job["enabled"] is True
        assert job["schedule"]["kind"] == "once"

        fetched = get_job(job["id"])
        assert fetched is not None
        assert fetched["prompt"] == "Check server status"

    def test_list_jobs(self, tmp_cron_dir):
        create_job(prompt="Job 1", schedule="every 1h")
        create_job(prompt="Job 2", schedule="every 2h")
        jobs = list_jobs()
        assert len(jobs) == 2

    def test_remove_job(self, tmp_cron_dir):
        job = create_job(prompt="Temp job", schedule="30m")
        assert remove_job(job["id"]) is True
        assert get_job(job["id"]) is None

    def test_remove_nonexistent_returns_false(self, tmp_cron_dir):
        assert remove_job("nonexistent") is False

    def test_auto_repeat_for_once(self, tmp_cron_dir):
        job = create_job(prompt="One-shot", schedule="1h")
        assert job["repeat"]["times"] == 1

    def test_interval_no_auto_repeat(self, tmp_cron_dir):
        job = create_job(prompt="Recurring", schedule="every 1h")
        assert job["repeat"]["times"] is None

    def test_default_delivery_origin(self, tmp_cron_dir):
        job = create_job(
            prompt="Test", schedule="30m",
            origin={"platform": "telegram", "chat_id": "123"},
        )
        assert job["deliver"] == "origin"

    def test_default_delivery_local_no_origin(self, tmp_cron_dir):
        job = create_job(prompt="Test", schedule="30m")
        assert job["deliver"] == "local"

    def test_create_persists_role_and_scope(self, tmp_cron_dir):
        job = create_job(
            prompt="Ship work",
            schedule="every 1h",
            role="Implement",
            scope="Ontology Workbench",
        )
        fetched = get_job(job["id"])
        assert fetched["role"] == "implement"
        assert fetched["scope"] == "ontology-workbench"


class TestUpdateJob:
    def test_update_name(self, tmp_cron_dir):
        job = create_job(prompt="Check server status", schedule="every 1h", name="Old Name")
        assert job["name"] == "Old Name"
        updated = update_job(job["id"], {"name": "New Name"})
        assert updated is not None
        assert isinstance(updated, dict)
        assert updated["name"] == "New Name"
        # Verify other fields are preserved
        assert updated["prompt"] == "Check server status"
        assert updated["id"] == job["id"]
        assert updated["schedule"] == job["schedule"]
        # Verify persisted to disk
        fetched = get_job(job["id"])
        assert fetched["name"] == "New Name"

    def test_update_schedule(self, tmp_cron_dir):
        job = create_job(prompt="Daily report", schedule="every 1h")
        assert job["schedule"]["kind"] == "interval"
        assert job["schedule"]["minutes"] == 60
        old_next_run = job["next_run_at"]
        new_schedule = parse_schedule("every 2h")
        updated = update_job(job["id"], {"schedule": new_schedule, "schedule_display": new_schedule["display"]})
        assert updated is not None
        assert updated["schedule"]["kind"] == "interval"
        assert updated["schedule"]["minutes"] == 120
        assert updated["schedule_display"] == "every 120m"
        assert updated["next_run_at"] != old_next_run
        # Verify persisted to disk
        fetched = get_job(job["id"])
        assert fetched["schedule"]["minutes"] == 120
        assert fetched["schedule_display"] == "every 120m"

    def test_update_enable_disable(self, tmp_cron_dir):
        job = create_job(prompt="Toggle me", schedule="every 1h")
        assert job["enabled"] is True
        updated = update_job(job["id"], {"enabled": False})
        assert updated["enabled"] is False
        fetched = get_job(job["id"])
        assert fetched["enabled"] is False

    def test_update_nonexistent_returns_none(self, tmp_cron_dir):
        result = update_job("nonexistent_id", {"name": "X"})
        assert result is None

    def test_update_role_and_scope_normalizes_values(self, tmp_cron_dir):
        job = create_job(prompt="Check server status", schedule="every 1h")
        updated = update_job(job["id"], {"role": "Report", "scope": "Global Ops"})
        assert updated["role"] == "report"
        assert updated["scope"] == "global-ops"


class TestPauseResumeJob:
    def test_pause_sets_state(self, tmp_cron_dir):
        job = create_job(prompt="Pause me", schedule="every 1h")
        paused = pause_job(job["id"], reason="user paused")
        assert paused is not None
        assert paused["enabled"] is False
        assert paused["state"] == "paused"
        assert paused["paused_reason"] == "user paused"

    def test_resume_reenables_job(self, tmp_cron_dir):
        job = create_job(prompt="Resume me", schedule="every 1h")
        pause_job(job["id"], reason="user paused")
        resumed = resume_job(job["id"])
        assert resumed is not None
        assert resumed["enabled"] is True
        assert resumed["state"] == "scheduled"
        assert resumed["paused_at"] is None
        assert resumed["paused_reason"] is None


class TestMarkJobRun:
    def test_increments_completed(self, tmp_cron_dir):
        job = create_job(prompt="Test", schedule="every 1h")
        mark_job_run(job["id"], success=True)
        updated = get_job(job["id"])
        assert updated["repeat"]["completed"] == 1
        assert updated["last_status"] == "ok"

    def test_repeat_limit_removes_job(self, tmp_cron_dir):
        job = create_job(prompt="Once", schedule="30m", repeat=1)
        mark_job_run(job["id"], success=True)
        # Job should be removed after hitting repeat limit
        assert get_job(job["id"]) is None

    def test_repeat_negative_one_is_infinite(self, tmp_cron_dir):
        # LLMs often pass repeat=-1 to mean "infinite/forever".
        # The job must NOT be deleted after runs when repeat <= 0.
        job = create_job(prompt="Forever", schedule="every 1h", repeat=-1)
        # -1 should be normalised to None (infinite) at create time
        assert job["repeat"]["times"] is None
        # Running it multiple times should never delete it
        for _ in range(3):
            mark_job_run(job["id"], success=True)
            assert get_job(job["id"]) is not None, "job was deleted after run despite infinite repeat"

    def test_repeat_zero_is_infinite(self, tmp_cron_dir):
        # repeat=0 should also be treated as None (infinite), not "run zero times".
        job = create_job(prompt="ZeroRepeat", schedule="every 1h", repeat=0)
        assert job["repeat"]["times"] is None
        mark_job_run(job["id"], success=True)
        assert get_job(job["id"]) is not None

    def test_error_status(self, tmp_cron_dir):
        job = create_job(prompt="Fail", schedule="every 1h")
        mark_job_run(job["id"], success=False, error="timeout")
        updated = get_job(job["id"])
        assert updated["last_status"] == "error"
        assert updated["last_error"] == "timeout"

    def test_delivery_error_tracked_separately(self, tmp_cron_dir):
        """Agent succeeds but delivery fails — both tracked independently."""
        job = create_job(prompt="Report", schedule="every 1h")
        mark_job_run(job["id"], success=True, delivery_error="platform 'telegram' not configured")
        updated = get_job(job["id"])
        assert updated["last_status"] == "ok"
        assert updated["last_error"] is None
        assert updated["last_delivery_error"] == "platform 'telegram' not configured"

    def test_delivery_error_cleared_on_success(self, tmp_cron_dir):
        """Successful delivery clears the previous delivery error."""
        job = create_job(prompt="Report", schedule="every 1h")
        mark_job_run(job["id"], success=True, delivery_error="network timeout")
        updated = get_job(job["id"])
        assert updated["last_delivery_error"] == "network timeout"
        # Next run delivers successfully
        mark_job_run(job["id"], success=True, delivery_error=None)
        updated = get_job(job["id"])
        assert updated["last_delivery_error"] is None

    def test_both_agent_and_delivery_error(self, tmp_cron_dir):
        """Agent fails AND delivery fails — both errors recorded."""
        job = create_job(prompt="Report", schedule="every 1h")
        mark_job_run(job["id"], success=False, error="model timeout",
                     delivery_error="platform 'discord' not enabled")
        updated = get_job(job["id"])
        assert updated["last_status"] == "error"
        assert updated["last_error"] == "model timeout"
        assert updated["last_delivery_error"] == "platform 'discord' not enabled"


class TestAdvanceNextRun:
    """Tests for advance_next_run() — crash-safety for recurring jobs."""

    def test_advances_interval_job(self, tmp_cron_dir):
        """Interval jobs should have next_run_at bumped to the next future occurrence."""
        job = create_job(prompt="Recurring check", schedule="every 1h")
        # Force next_run_at to 5 minutes ago (i.e. the job is due)
        jobs = load_jobs()
        old_next = (datetime.now() - timedelta(minutes=5)).isoformat()
        jobs[0]["next_run_at"] = old_next
        save_jobs(jobs)

        result = advance_next_run(job["id"])
        assert result is True

        updated = get_job(job["id"])
        from cron.jobs import _ensure_aware, _hermes_now
        new_next_dt = _ensure_aware(datetime.fromisoformat(updated["next_run_at"]))
        assert new_next_dt > _hermes_now(), "next_run_at should be in the future after advance"

    def test_advances_cron_job(self, tmp_cron_dir):
        """Cron-expression jobs should have next_run_at bumped to the next occurrence."""
        pytest.importorskip("croniter")
        job = create_job(prompt="Daily wakeup", schedule="15 6 * * *")
        # Force next_run_at to 30 minutes ago
        jobs = load_jobs()
        old_next = (datetime.now() - timedelta(minutes=30)).isoformat()
        jobs[0]["next_run_at"] = old_next
        save_jobs(jobs)

        result = advance_next_run(job["id"])
        assert result is True

        updated = get_job(job["id"])
        from cron.jobs import _ensure_aware, _hermes_now
        new_next_dt = _ensure_aware(datetime.fromisoformat(updated["next_run_at"]))
        assert new_next_dt > _hermes_now(), "next_run_at should be in the future after advance"

    def test_skips_oneshot_job(self, tmp_cron_dir):
        """One-shot jobs should NOT be advanced — they need to retry on restart."""
        job = create_job(prompt="Run once", schedule="30m")
        original_next = get_job(job["id"])["next_run_at"]

        result = advance_next_run(job["id"])
        assert result is False

        updated = get_job(job["id"])
        assert updated["next_run_at"] == original_next, "one-shot next_run_at should be unchanged"

    def test_nonexistent_job_returns_false(self, tmp_cron_dir):
        result = advance_next_run("nonexistent-id")
        assert result is False

    def test_already_future_stays_future(self, tmp_cron_dir):
        """If next_run_at is already in the future, advance keeps it in the future (no harm)."""
        job = create_job(prompt="Future job", schedule="every 1h")
        # next_run_at is already set to ~1h from now by create_job
        advance_next_run(job["id"])
        # Regardless of return value, the job should still be in the future
        updated = get_job(job["id"])
        from cron.jobs import _ensure_aware, _hermes_now
        new_next_dt = _ensure_aware(datetime.fromisoformat(updated["next_run_at"]))
        assert new_next_dt > _hermes_now(), "next_run_at should remain in the future"

    def test_crash_safety_scenario(self, tmp_cron_dir):
        """Simulate the crash-loop scenario: after advance, the job should NOT be due."""
        job = create_job(prompt="Crash test", schedule="every 1h")
        # Force next_run_at to 5 minutes ago (job is due)
        jobs = load_jobs()
        jobs[0]["next_run_at"] = (datetime.now() - timedelta(minutes=5)).isoformat()
        save_jobs(jobs)

        # Job should be due before advance
        due_before = get_due_jobs()
        assert len(due_before) == 1

        # Advance (simulating what tick() does before run_job)
        advance_next_run(job["id"])

        # Now the job should NOT be due (simulates restart after crash)
        due_after = get_due_jobs()
        assert len(due_after) == 0, "Job should not be due after advance_next_run"


class TestGetDueJobs:
    def test_past_due_within_window_returned(self, tmp_cron_dir):
        """Jobs within the dynamic grace window are still considered due (not stale).

        For an hourly job, grace = 30 min (half the period, clamped to [120s, 2h]).
        """
        job = create_job(prompt="Due now", schedule="every 1h")
        # Force next_run_at to 10 minutes ago (within the 30-min grace for hourly)
        jobs = load_jobs()
        jobs[0]["next_run_at"] = (datetime.now() - timedelta(minutes=10)).isoformat()
        save_jobs(jobs)

        due = get_due_jobs()
        assert len(due) == 1
        assert due[0]["id"] == job["id"]

    def test_stale_past_due_skipped(self, tmp_cron_dir):
        """Recurring jobs past their dynamic grace window are fast-forwarded, not fired.

        For an hourly job, grace = 30 min. Setting 35 min late exceeds the window.
        """
        job = create_job(prompt="Stale", schedule="every 1h")
        # Force next_run_at to 35 minutes ago (beyond the 30-min grace for hourly)
        jobs = load_jobs()
        jobs[0]["next_run_at"] = (datetime.now() - timedelta(minutes=35)).isoformat()
        save_jobs(jobs)

        due = get_due_jobs()
        assert len(due) == 0
        # next_run_at should be fast-forwarded to the future
        updated = get_job(job["id"])
        from cron.jobs import _ensure_aware, _hermes_now
        next_dt = _ensure_aware(datetime.fromisoformat(updated["next_run_at"]))
        assert next_dt > _hermes_now()

    def test_future_not_returned(self, tmp_cron_dir):
        create_job(prompt="Not yet", schedule="every 1h")
        due = get_due_jobs()
        assert len(due) == 0

    def test_disabled_not_returned(self, tmp_cron_dir):
        job = create_job(prompt="Disabled", schedule="every 1h")
        jobs = load_jobs()
        jobs[0]["enabled"] = False
        jobs[0]["next_run_at"] = (datetime.now() - timedelta(minutes=5)).isoformat()
        save_jobs(jobs)

        due = get_due_jobs()
        assert len(due) == 0

    def test_broken_recent_one_shot_without_next_run_is_recovered(self, tmp_cron_dir, monkeypatch):
        now = datetime(2026, 3, 18, 4, 22, 30, tzinfo=timezone.utc)
        monkeypatch.setattr("cron.jobs._hermes_now", lambda: now)

        run_at = "2026-03-18T04:22:00+00:00"
        save_jobs(
            [{
                "id": "oneshot-recover",
                "name": "Recover me",
                "prompt": "Word of the day",
                "schedule": {"kind": "once", "run_at": run_at, "display": "once at 2026-03-18 04:22"},
                "schedule_display": "once at 2026-03-18 04:22",
                "repeat": {"times": 1, "completed": 0},
                "enabled": True,
                "state": "scheduled",
                "paused_at": None,
                "paused_reason": None,
                "created_at": "2026-03-18T04:21:00+00:00",
                "next_run_at": None,
                "last_run_at": None,
                "last_status": None,
                "last_error": None,
                "deliver": "local",
                "origin": None,
            }]
        )

        due = get_due_jobs()

        assert [job["id"] for job in due] == ["oneshot-recover"]
        assert get_job("oneshot-recover")["next_run_at"] == run_at

    def test_broken_stale_one_shot_without_next_run_is_not_recovered(self, tmp_cron_dir, monkeypatch):
        now = datetime(2026, 3, 18, 4, 30, 0, tzinfo=timezone.utc)
        monkeypatch.setattr("cron.jobs._hermes_now", lambda: now)

        save_jobs(
            [{
                "id": "oneshot-stale",
                "name": "Too old",
                "prompt": "Word of the day",
                "schedule": {"kind": "once", "run_at": "2026-03-18T04:22:00+00:00", "display": "once at 2026-03-18 04:22"},
                "schedule_display": "once at 2026-03-18 04:22",
                "repeat": {"times": 1, "completed": 0},
                "enabled": True,
                "state": "scheduled",
                "paused_at": None,
                "paused_reason": None,
                "created_at": "2026-03-18T04:21:00+00:00",
                "next_run_at": None,
                "last_run_at": None,
                "last_status": None,
                "last_error": None,
                "deliver": "local",
                "origin": None,
            }]
        )

        assert get_due_jobs() == []
        assert get_job("oneshot-stale")["next_run_at"] is None


class TestInspectJobTopology:
    def _write_output(self, job_id: str, filename: str, response: str):
        import cron.jobs as cron_jobs

        output_dir = cron_jobs.OUTPUT_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / filename
        path.write_text(f"# Cron Job\n\n## Response\n\n{response}", encoding="utf-8")
        return path

    def test_duplicate_names_are_flagged(self, tmp_cron_dir):
        first = create_job(prompt="A", schedule="every 1h", name="shared-name")
        second = create_job(prompt="B", schedule="every 2h", name="shared-name")
        pause_job(second["id"], reason="legacy duplicate")

        snapshot = inspect_job_topology(include_disabled=True)

        duplicate_issue = next(issue for issue in snapshot["issues"] if issue["code"] == "duplicate_job_name")
        assert duplicate_issue["severity"] == "warning"
        assert first["id"] in duplicate_issue["job_ids"]
        assert second["id"] in duplicate_issue["job_ids"]

    def test_duplicate_implementation_scope_is_error(self, tmp_cron_dir):
        create_job(prompt="A", schedule="every 1h", name="impl-a", role="implement", scope="pipeline")
        create_job(prompt="B", schedule="every 2h", name="impl-b", role="implement", scope="pipeline")

        snapshot = inspect_job_topology(include_disabled=True)

        issue = next(issue for issue in snapshot["issues"] if issue["code"] == "duplicate_implementation_scope")
        assert issue["severity"] == "error"
        assert issue["scope"] == "pipeline"
        assert snapshot["ok"] is False

    def test_global_implementer_overlapping_scoped_implementers_is_error(self, tmp_cron_dir):
        create_job(prompt="A", schedule="every 1h", name="impl-global", role="implement", scope="global")
        create_job(prompt="B", schedule="every 2h", name="impl-ontology", role="implement", scope="ontology")

        snapshot = inspect_job_topology(include_disabled=True)

        issue = next(issue for issue in snapshot["issues"] if issue["code"] == "global_implementation_overlap")
        assert issue["severity"] == "error"
        assert snapshot["ok"] is False

    def test_duplicate_global_coordinators_are_flagged(self, tmp_cron_dir):
        create_job(prompt="A", schedule="every 1h", name="coord-a", role="coordinate", scope="global")
        create_job(prompt="B", schedule="every 2h", name="coord-b", role="coordinate", scope="global")

        snapshot = inspect_job_topology(include_disabled=True)

        issue = next(issue for issue in snapshot["issues"] if issue["code"] == "duplicate_global_coordinator")
        assert issue["severity"] == "error"
        assert snapshot["ok"] is False

    def test_global_coordinator_overlapping_scoped_implementers_is_error(self, tmp_cron_dir):
        create_job(prompt="A", schedule="every 1h", name="coord-global", role="coordinate", scope="global")
        create_job(prompt="B", schedule="every 2h", name="impl-pipeline", role="implement", scope="pipeline")

        snapshot = inspect_job_topology(include_disabled=True)

        issue = next(
            issue for issue in snapshot["issues"] if issue["code"] == "global_coordinator_with_scoped_implementers"
        )
        assert issue["severity"] == "error"
        assert snapshot["ok"] is False

    def test_persistence_ratchet_healthy_when_state_carries_forward(self, tmp_cron_dir):
        job = create_job(
            prompt="Coordinate backlog",
            schedule="every 1h",
            name="coord-global",
            role="coordinate",
            scope="global",
        )
        first = """Progress report.

Persistence Ratchet:
- Evidence: HAD-420 asks for preserved evidence/decisions/artifacts across runs
- Decisions: keep one global coordinator as backlog owner
- Artifacts: hadto_patches/cron_jobs.py topology check
- Carry-forward: preserve HAD-420 evidence in the next report
- Drift: none
"""
        second = """Follow-up report.

Persistence Ratchet:
- Evidence: HAD-420 asks for preserved evidence/decisions/artifacts across runs
- Decisions: keep one global coordinator as backlog owner
- Artifacts: hadto_patches/cron_jobs.py topology check
- Carry-forward: keep HAD-420 evidence attached to the next action
- Drift: none
"""
        self._write_output(job["id"], "2026-04-21_10-00-00.md", first)
        self._write_output(job["id"], "2026-04-21_11-00-00.md", second)

        ratchet = inspect_persistence_ratchet(get_job(job["id"]))
        snapshot = inspect_job_topology(include_disabled=True)

        assert ratchet["status"] == "healthy"
        assert ratchet["compact_evidence"]["preserved_item_count"] >= 3
        assert snapshot["summary"]["persistence_ratchet_checked"] == 1
        assert snapshot["summary"]["persistence_ratchet_issue_count"] == 0
        assert not [issue for issue in snapshot["issues"] if issue["code"].startswith("persistence_ratchet")]

    def test_persistence_ratchet_missing_is_warning_after_repeated_reports(self, tmp_cron_dir):
        job = create_job(
            prompt="Coordinate backlog",
            schedule="every 1h",
            name="coord-global",
            role="coordinate",
            scope="global",
        )
        self._write_output(job["id"], "2026-04-21_10-00-00.md", "Checked the backlog and found work.")
        self._write_output(job["id"], "2026-04-21_11-00-00.md", "Checked the backlog and found work again.")

        snapshot = inspect_job_topology(include_disabled=True)

        issue = next(issue for issue in snapshot["issues"] if issue["code"] == "persistence_ratchet_missing")
        assert issue["severity"] == "warning"
        assert issue["job_id"] == job["id"]
        assert issue["surfaces"] == ["operator_value", "anti_make_work", "leading_indicator"]
        assert snapshot["ok"] is True

    def test_trust_contract_surfaces_repeated_loop_posture_and_artifact(self, tmp_cron_dir):
        job = create_job(
            prompt="Coordinate backlog and keep the selected issue moving.",
            schedule="every 10m",
            name="coord-global",
            role="coordinate",
            scope="global",
        )
        self._write_output(job["id"], "2026-04-21_10-00-00.md", "Checked the backlog and found work.")
        self._write_output(job["id"], "2026-04-21_11-00-00.md", "Checked the backlog and found work again.")

        ratchet = inspect_persistence_ratchet(get_job(job["id"]))
        contract = inspect_trust_contract(get_job(job["id"]), ratchet)
        snapshot = inspect_job_topology(include_disabled=True)
        topology_contract = next(item for item in snapshot["trust_contracts"] if item["job_id"] == job["id"])

        assert contract["declared_commitment"].startswith("Coordinate backlog")
        assert contract["interaction_mode"] == "repeated"
        assert contract["discovery_execution_mode"] == "bridge"
        assert contract["shared_artifact_path"].endswith(job["id"])
        assert contract["verification_target"].startswith("saved output in")
        assert contract["trust_posture"] == "repeated_trust_bearing_degraded"
        assert contract["fast_loop_surfaces"][0].startswith("reacquire backlog")
        assert contract["slow_loop_surfaces"][-1] == "change backlog selection policy, preemption rules, or recurring loop contracts"
        assert "backlog policy" in contract["escalation_checkpoint"]
        assert topology_contract["trust_posture"] == contract["trust_posture"]
        assert topology_contract["fast_loop_surfaces"] == contract["fast_loop_surfaces"]
        assert topology_contract["escalation_checkpoint"] == contract["escalation_checkpoint"]
        assert snapshot["summary"]["trust_contract_checked"] >= 1
        assert snapshot["summary"]["trust_contract_degraded_count"] >= 1

    def test_first_proof_point_populates_bounded_seed_in_trust_contract(self, tmp_cron_dir):
        job = create_job(
            prompt="Coordinate backlog and keep the selected issue moving.",
            schedule="every 10m",
            name="coord-global",
            role="coordinate",
            scope="global",
        )
        self._write_output(
            job["id"],
            "2026-04-21_11-00-00.md",
            """Backlog coordination moved HAD-437.

First Proof Point:
- Seed Surface: HAD-437 cron topology report in hadto_patches/cron_jobs.py and hermes cron topology
- Protection Assumptions: limited to recurring classified cron control loops; warnings only, no job rewrites
- Success Signal: topology output names the first seed, proof signal, and imitation dependency
- Imitation Path: copy the same field set into other planning/self-improvement surfaces after topology reports stay populated
- Why First: the global coordinator is the recurring bridge loop most likely to turn governance language into live work
""",
        )

        proof_point = inspect_first_proof_point(get_job(job["id"]))
        snapshot = inspect_job_topology(include_disabled=True)
        contract = next(item for item in snapshot["trust_contracts"] if item["job_id"] == job["id"])

        assert proof_point["status"] == "populated"
        assert proof_point["fields"]["seed_surface"].startswith("HAD-437 cron topology report")
        assert proof_point["fields"]["imitation_path"].startswith("copy the same field set")
        assert contract["first_proof_point"]["status"] == "populated"
        assert contract["first_proof_point"]["fields"]["success_signal"].startswith("topology output names")
        assert snapshot["summary"]["first_proof_point_checked"] == 1
        assert snapshot["summary"]["first_proof_point_issue_count"] == 0

    def test_first_proof_point_missing_is_warning_after_report(self, tmp_cron_dir):
        job = create_job(
            prompt="Coordinate backlog",
            schedule="every 1h",
            name="coord-global",
            role="coordinate",
            scope="global",
        )
        self._write_output(job["id"], "2026-04-21_11-00-00.md", "Governance should improve everywhere.")

        snapshot = inspect_job_topology(include_disabled=True)

        issue = next(issue for issue in snapshot["issues"] if issue["code"] == "first_proof_point_missing")
        assert issue["severity"] == "warning"
        assert issue["job_id"] == job["id"]
        assert "one protected seed surface" in issue["message"]
        assert snapshot["summary"]["first_proof_point_issue_count"] == 1

    def test_persistence_ratchet_surfaces_repeated_rediscovery_and_cleanup_drift(self, tmp_cron_dir):
        job = create_job(
            prompt="Coordinate backlog",
            schedule="every 1h",
            name="coord-global",
            role="coordinate",
            scope="global",
        )
        self._write_output(
            job["id"],
            "2026-04-21_10-00-00.md",
            "Rediscovered the same backlog gap again and cleaned a dirty checkout.",
        )
        self._write_output(
            job["id"],
            "2026-04-21_11-00-00.md",
            "Rediscovered the same backlog gap again; cleanup drift left untracked files.",
        )

        snapshot = inspect_job_topology(include_disabled=True)

        issue = next(issue for issue in snapshot["issues"] if issue["code"] == "persistence_ratchet_drift")
        evidence = issue["compact_evidence"]
        assert issue["ratchet_status"] == "drift"
        assert evidence["repeated_signals"] == ["cleanup_drift", "repeated_rediscovery"]


class TestSaveJobOutput:
    def test_creates_output_file(self, tmp_cron_dir):
        output_file = save_job_output("test123", "# Results\nEverything ok.")
        assert output_file.exists()
        assert output_file.read_text() == "# Results\nEverything ok."
        assert "test123" in str(output_file)
