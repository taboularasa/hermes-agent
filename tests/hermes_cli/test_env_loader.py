import importlib
import os
import subprocess
import sys

import pytest

import hermes_cli.env_loader as env_loader


def _doppler_result(payload: str, *, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["doppler", "secrets", "download"],
        returncode=returncode,
        stdout=payload,
        stderr=stderr,
    )


def test_loads_doppler_env_into_process(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()

    monkeypatch.delenv("HERMES_ENV_SOURCE", raising=False)
    monkeypatch.delenv("HERMES_DOPPLER_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(env_loader.shutil, "which", lambda _: "/usr/bin/doppler")
    monkeypatch.setattr(
        env_loader.subprocess,
        "run",
        lambda *args, **kwargs: _doppler_result(
            '{"OPENAI_BASE_URL":"https://doppler.example/v1","OPENAI_API_KEY":"doppler-key"}'
        ),
    )

    loaded = env_loader.load_hermes_dotenv(project_env=project_root / ".env", strict=True)

    assert loaded == [f"doppler:{project_root}"]
    assert os.getenv("OPENAI_BASE_URL") == "https://doppler.example/v1"
    assert os.getenv("OPENAI_API_KEY") == "doppler-key"
    assert os.getenv("HERMES_ENV_SOURCE") == "doppler"
    assert os.getenv("HERMES_DOPPLER_PROJECT_ROOT") == str(project_root)


def test_hard_fails_when_doppler_is_not_configured(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()

    monkeypatch.setattr(env_loader.shutil, "which", lambda _: "/usr/bin/doppler")
    monkeypatch.setattr(
        env_loader.subprocess,
        "run",
        lambda *args, **kwargs: _doppler_result(
            "",
            returncode=1,
            stderr="You must specify a project",
        ),
    )

    with pytest.raises(RuntimeError, match="Doppler"):
        env_loader.load_hermes_dotenv(project_env=project_root / ".env", strict=True)


def test_non_strict_mode_skips_missing_doppler(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(env_loader.shutil, "which", lambda _: "/usr/bin/doppler")
    monkeypatch.setattr(
        env_loader.subprocess,
        "run",
        lambda *args, **kwargs: _doppler_result(
            "",
            returncode=1,
            stderr="You must specify a project",
        ),
    )

    loaded = env_loader.load_hermes_dotenv(project_env=project_root / ".env", strict=False)

    assert loaded == []
    assert os.getenv("OPENAI_BASE_URL") is None


def test_main_import_applies_doppler_env_over_existing_values(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "openrouter")
    monkeypatch.delenv("HERMES_ENV_SOURCE", raising=False)
    monkeypatch.delenv("HERMES_DOPPLER_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(env_loader.shutil, "which", lambda _: "/usr/bin/doppler")
    monkeypatch.setattr(
        env_loader.subprocess,
        "run",
        lambda *args, **kwargs: _doppler_result(
            '{"OPENAI_BASE_URL":"https://doppler.example/v1","HERMES_INFERENCE_PROVIDER":"custom"}'
        ),
    )
    monkeypatch.setattr(env_loader, "_resolve_project_root", lambda _project_env: project_root)

    sys.modules.pop("hermes_cli.main", None)
    importlib.import_module("hermes_cli.main")

    assert os.getenv("OPENAI_BASE_URL") == "https://doppler.example/v1"
    assert os.getenv("HERMES_INFERENCE_PROVIDER") == "custom"
