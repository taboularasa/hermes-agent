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
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_KEY")


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


def _safe_which(cmd: str) -> str | None:
    try:
        return shutil.which(cmd)
    except (AttributeError, ImportError):
        return None


def _sanitize_loaded_credentials() -> None:
    """Strip non-ASCII characters from loaded credential env vars."""
    from hermes_cli.config import _check_non_ascii_credential

    for key, value in list(os.environ.items()):
        if not any(key.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES):
            continue
        os.environ[key] = _check_non_ascii_credential(key, value)


def _sanitize_env_file_if_needed(env_path: str | os.PathLike) -> int:
    """Best-effort compatibility sanitizer for local .env files in test mode."""
    path = Path(env_path)
    if not path.exists():
        return 0

    from hermes_cli.config import _sanitize_env_lines

    original_lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    sanitized_lines = _sanitize_env_lines(original_lines)
    if sanitized_lines == original_lines:
        return 0

    path.write_text("".join(sanitized_lines), encoding="utf-8")
    return abs(len(sanitized_lines) - len(original_lines)) or sum(
        1 for a, b in zip(original_lines, sanitized_lines) if a != b
    )


def _load_env_file_values(env_path: str | os.PathLike) -> dict[str, str]:
    """Load a local .env file for non-strict/test fallback paths."""
    path = Path(env_path)
    if not path.exists():
        return {}

    _sanitize_env_file_if_needed(path)

    try:
        from dotenv import dotenv_values

        raw_values = dotenv_values(path)
        values = {
            str(key): "" if value is None else str(value)
            for key, value in raw_values.items()
            if key
        }
        for key, value in list(values.items()):
            if any(key.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES):
                from hermes_cli.config import _check_non_ascii_credential

                values[key] = _check_non_ascii_credential(key, value)
        return values
    except Exception:
        result: dict[str, str] = {}
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if any(key.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES):
                from hermes_cli.config import _check_non_ascii_credential

                value = _check_non_ascii_credential(key, value)
            result[key] = value
        return result


def _download_doppler_env(project_root: Path) -> dict[str, str]:
    doppler_path = _safe_which("doppler")
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
    _sanitize_loaded_credentials()

    os.environ["HERMES_ENV_SOURCE"] = _SOURCE_LABEL
    os.environ["HERMES_DOPPLER_PROJECT_ROOT"] = str(project_root)
    return [source_label]


def doppler_required(strict: bool | None = None) -> bool:
    """Public wrapper for the Doppler strictness policy."""
    return _doppler_required(strict)


def load_runtime_env(
    *,
    hermes_home: str | os.PathLike | None = None,
    project_env: str | os.PathLike | None = None,
    strict: bool | None = None,
) -> dict[str, str]:
    """Load runtime secrets and return the effective process environment."""
    load_hermes_dotenv(hermes_home=hermes_home, project_env=project_env, strict=strict)
    return dict(os.environ)


def ensure_env_write_allowed(key: str) -> None:
    """Reject local .env writes when Hermes is configured for Doppler-only secrets."""
    if doppler_required(None):
        raise RuntimeError(
            f"Hermes is configured for Doppler-only secrets. Update {key} in Doppler instead of ~/.hermes/.env."
        )


def get_runtime_env_value(key: str) -> str | None:
    """Resolve an env var from the live process env, loading Doppler on demand."""
    if key in os.environ:
        return os.environ[key]
    if not doppler_required(None):
        try:
            from hermes_cli.config import get_env_path

            return _load_env_file_values(get_env_path()).get(key)
        except Exception:
            return os.environ.get(key)
    return load_runtime_env(strict=False).get(key)
