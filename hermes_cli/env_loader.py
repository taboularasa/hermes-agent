"""Helpers for loading Hermes secrets from Doppler across entrypoints."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

_FALSEY = {"", "0", "false", "no", "off"}
_DOPPLER_TIMEOUT_SECONDS = 15
_SOURCE_LABEL = "doppler"


def _doppler_required(strict: bool | None) -> bool:
    if strict is not None:
        return strict
    return os.getenv("HERMES_REQUIRE_DOPPLER", "1").strip().lower() not in _FALSEY


def _resolve_project_root(project_env: str | os.PathLike | None) -> Path:
    if project_env is None:
        return Path(__file__).resolve().parent.parent
    path = Path(project_env)
    return path if path.is_dir() else path.parent


def _format_doppler_error(project_root: Path, detail: str) -> str:
    return (
        f"Hermes requires Doppler secrets and will not fall back to .env files. "
        f"Failed to load Doppler for {project_root}: {detail}. "
        f"Configure Doppler for this repo or launch Hermes under `doppler run -- ...`."
    )


def _download_doppler_env(project_root: Path) -> dict[str, str]:
    doppler_path = shutil.which("doppler")
    if not doppler_path:
        raise RuntimeError(
            _format_doppler_error(project_root, "the `doppler` CLI is not installed")
        )

    command = [
        doppler_path,
        "secrets",
        "download",
        "--format",
        "json",
        "--no-file",
        "--silent",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=_DOPPLER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            _format_doppler_error(project_root, f"Doppler timed out after {exc.timeout}s")
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            _format_doppler_error(project_root, f"failed to execute Doppler ({exc})")
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        raise RuntimeError(_format_doppler_error(project_root, detail))

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            _format_doppler_error(project_root, "Doppler returned invalid JSON")
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            _format_doppler_error(project_root, "Doppler returned a non-object payload")
        )

    return {str(key): "" if value is None else str(value) for key, value in payload.items()}


def load_hermes_dotenv(
    *,
    hermes_home: str | os.PathLike | None = None,
    project_env: str | os.PathLike | None = None,
    strict: bool | None = None,
) -> list[str]:
    """Load Hermes secrets from Doppler.

    Hermes no longer reads ``~/.hermes/.env`` at startup. Runtime secrets must
    come from Doppler. In strict mode, failure to load Doppler raises.
    Non-strict mode exists so the test suite can import modules without a real
    Doppler configuration.
    """

    del hermes_home  # preserved for call-site compatibility

    project_root = _resolve_project_root(project_env)
    source_label = f"{_SOURCE_LABEL}:{project_root}"

    if (
        os.getenv("HERMES_ENV_SOURCE") == _SOURCE_LABEL
        and os.getenv("HERMES_DOPPLER_PROJECT_ROOT") == str(project_root)
    ):
        return [source_label]

    required = _doppler_required(strict)
    try:
        env_vars = _download_doppler_env(project_root)
    except RuntimeError:
        if required:
            raise
        return []

    for key, value in env_vars.items():
        os.environ[key] = value

    os.environ["HERMES_ENV_SOURCE"] = _SOURCE_LABEL
    os.environ["HERMES_DOPPLER_PROJECT_ROOT"] = str(project_root)
    return [source_label]
