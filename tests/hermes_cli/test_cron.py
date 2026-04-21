"""Tests for hermes_cli.cron command handling."""

import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from cron.jobs import create_job, get_job, list_jobs
from hermes_cli.cron import cron_command


@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.delenv("HERMES_CRON_EXECUTION_CONTEXT", raising=False)
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    monkeypatch.delenv("HERMES_CRON_AUTO_DELIVER_CHAT_ID", raising=False)
    monkeypatch.delenv("HERMES_CRON_AUTO_DELIVER_PLATFORM", raising=False)
    monkeypatch.delenv("HERMES_CRON_AUTO_DELIVER_THREAD_ID", raising=False)
    return tmp_path


class TestCronCommandLifecycle:
    def test_pause_resume_run(self, tmp_cron_dir, capsys):
        job = create_job(prompt="Check server status", schedule="every 1h")

        cron_command(Namespace(cron_command="pause", job_id=job["id"], reason="maintenance window"))
        paused = get_job(job["id"])
        assert paused["state"] == "paused"
        assert paused["paused_reason"] == "maintenance window"

        cron_command(Namespace(cron_command="resume", job_id=job["id"]))
        resumed = get_job(job["id"])
        assert resumed["state"] == "scheduled"

        cron_command(Namespace(cron_command="run", job_id=job["id"]))
        triggered = get_job(job["id"])
        assert triggered["state"] == "scheduled"

        out = capsys.readouterr().out
        assert "Paused job" in out
        assert "maintenance window" in out
        assert "Resumed job" in out
        assert "Triggered job" in out

    def test_edit_can_replace_and_clear_skills(self, tmp_cron_dir, capsys):
        job = create_job(
            prompt="Combine skill outputs",
            schedule="every 1h",
            skill="blogwatcher",
        )

        cron_command(
            Namespace(
                cron_command="edit",
                job_id=job["id"],
                schedule="every 2h",
                prompt="Revised prompt",
                name="Edited Job",
                deliver=None,
                repeat=None,
                skill=None,
                skills=["find-nearby", "blogwatcher"],
                clear_skills=False,
            )
        )
        updated = get_job(job["id"])
        assert updated["skills"] == ["find-nearby", "blogwatcher"]
        assert updated["name"] == "Edited Job"
        assert updated["prompt"] == "Revised prompt"
        assert updated["schedule_display"] == "every 120m"

        cron_command(
            Namespace(
                cron_command="edit",
                job_id=job["id"],
                schedule=None,
                prompt=None,
                name=None,
                deliver=None,
                repeat=None,
                skill=None,
                skills=None,
                clear_skills=True,
            )
        )
        cleared = get_job(job["id"])
        assert cleared["skills"] == []
        assert cleared["skill"] is None

        out = capsys.readouterr().out
        assert "Updated job" in out

    def test_create_with_multiple_skills(self, tmp_cron_dir, capsys):
        cron_command(
            Namespace(
                cron_command="create",
                schedule="every 1h",
                prompt="Use both skills",
                name="Skill combo",
                deliver=None,
                repeat=None,
                skill=None,
                skills=["blogwatcher", "find-nearby"],
                role=None,
                scope=None,
            )
        )
        out = capsys.readouterr().out
        assert "Created job" in out

        jobs = list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["skills"] == ["blogwatcher", "find-nearby"]
        assert jobs[0]["name"] == "Skill combo"

    def test_doctor_reports_clean_topology(self, tmp_cron_dir, capsys):
        create_job(prompt="Implement", schedule="every 1h", name="impl", role="implement", scope="ontology")

        exit_code = cron_command(Namespace(cron_command="doctor", all=True))
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "no conflicts detected" in out.lower()

    def test_main_parser_accepts_topology_and_doctor(self, tmp_path):
        repo_root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["HERMES_HOME"] = str(tmp_path / ".hermes")
        env.pop("HERMES_CRON_EXECUTION_CONTEXT", None)
        env.pop("HERMES_CRON_SESSION", None)
        env.pop("HERMES_CRON_AUTO_DELIVER_CHAT_ID", None)
        env.pop("HERMES_CRON_AUTO_DELIVER_PLATFORM", None)

        for args in (["cron", "topology", "--all"], ["cron", "doctor"]):
            result = subprocess.run(
                [sys.executable, "-m", "hermes_cli.main", *args],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert result.returncode == 0, result.stderr
