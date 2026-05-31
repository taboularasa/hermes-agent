#!/usr/bin/env python3
"""Build a no-mutation dry-run plan for the Hermes host data migration.

The script collects preflight manifests and, only when guards pass, writes and
prints the command plan an operator would run later. It never changes source
paths, /data, systemd, Docker, /etc/fstab, or git repositories.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, TextIO


REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_ID = "${MIGRATION_ID}"
WRITER_KEYWORDS = (
    "hermes",
    "codex",
    "hadto",
    "run_agent",
    "gateway",
    "worker",
    "cron",
    "pytest",
    "uv",
    "npm",
    "node",
    "python",
)
SYSTEMD_KEYWORDS = ("hermes", "codex", "hadto")


@dataclass(frozen=True)
class RunResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: str = ""


@dataclass(frozen=True)
class ManagedRoot:
    label: str
    source: Path
    target: Path
    holdback: str
    bind_target: Path
    fstab_line: str
    exists: bool


@dataclass(frozen=True)
class MountInfo:
    path: Path
    exists: bool
    is_mount: bool
    fstype: str = ""
    source: str = ""
    uuid: str = ""
    options: str = ""
    error: str = ""


@dataclass(frozen=True)
class SystemdUnit:
    scope: str
    name: str
    active: str
    sub: str
    description: str = ""


@dataclass(frozen=True)
class DockerMount:
    type: str
    source: str
    destination: str
    name: str = ""
    matches_managed_root: bool = False


@dataclass(frozen=True)
class DockerContainer:
    id: str
    name: str
    mounts: list[DockerMount] = field(default_factory=list)


@dataclass(frozen=True)
class WriterFinding:
    kind: str
    detail: str


@dataclass(frozen=True)
class DryRunOptions:
    output_dir: Path
    home_root: Path = Path("/home/david")
    data_mount: Path = Path("/data")
    target_root: Path = Path("/data/hermes")
    allow_active_writers: bool = False
    du_timeout: int = 120


class CommandRunner:
    def __call__(self, argv: list[str], timeout: int = 30) -> RunResult:
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            return RunResult(argv=argv, returncode=127, error=str(exc))
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return RunResult(
                argv=argv,
                returncode=124,
                stdout=stdout,
                stderr=stderr,
                error=f"timed out after {timeout}s",
            )
        except Exception as exc:  # pragma: no cover - defensive manifest path
            return RunResult(argv=argv, returncode=1, error=repr(exc))
        return RunResult(
            argv=argv,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


Runner = Callable[[list[str], int], RunResult]
ProcessScanner = Callable[[list[ManagedRoot]], list[WriterFinding]]


def _quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def _path_is_under(path: str | Path, root: str | Path) -> bool:
    try:
        path_abs = os.path.abspath(os.fspath(path))
        root_abs = os.path.abspath(os.fspath(root))
        return os.path.commonpath([path_abs, root_abs]) == root_abs
    except (OSError, ValueError):
        return False


def _safe_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_default(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def discover_managed_roots(home_root: Path, target_root: Path) -> list[ManagedRoot]:
    specs = (
        ("profile-default", home_root / ".hermes", "profile-default"),
        ("stacks", home_root / "stacks", "stacks"),
        ("ctx-data", home_root / ".ctx-data", "ctx-data"),
        ("codex-home", home_root / ".codex", "codex-home"),
    )
    roots: list[ManagedRoot] = []
    for label, source, target_name in specs:
        target = target_root / target_name
        holdback = f"{source}.pre-data-migration-holdback.{MIGRATION_ID}"
        fstab_line = (
            f"{target} {source} none "
            f"bind,x-systemd.requires-mounts-for={target_root.parent} 0 0"
        )
        roots.append(
            ManagedRoot(
                label=label,
                source=source,
                target=target,
                holdback=holdback,
                bind_target=source,
                fstab_line=fstab_line,
                exists=source.exists(),
            )
        )
    return roots


def probe_mount(path: Path, runner: Runner) -> MountInfo:
    result = runner(
        ["findmnt", "-J", "-T", str(path), "-o", "TARGET,SOURCE,FSTYPE,OPTIONS,UUID"],
        20,
    )
    exists = path.exists()
    if result.returncode != 0 or not result.stdout.strip():
        error = result.error or result.stderr.strip() or "findmnt returned no mount"
        return MountInfo(path=path, exists=exists, is_mount=False, error=error)
    try:
        payload = json.loads(result.stdout)
        filesystem = (payload.get("filesystems") or [{}])[0]
    except (json.JSONDecodeError, IndexError, AttributeError) as exc:
        return MountInfo(
            path=path,
            exists=exists,
            is_mount=False,
            error=f"could not parse findmnt JSON: {exc}",
        )
    return MountInfo(
        path=path,
        exists=exists,
        is_mount=True,
        fstype=str(filesystem.get("fstype") or ""),
        source=str(filesystem.get("source") or ""),
        uuid=str(filesystem.get("uuid") or ""),
        options=str(filesystem.get("options") or ""),
    )


def _format_result(result: RunResult) -> str:
    out = io.StringIO()
    out.write(f"$ {' '.join(_quote(part) for part in result.argv)}\n")
    out.write(f"exit_code={result.returncode}\n")
    if result.error:
        out.write(f"error={result.error}\n")
    if result.stdout:
        out.write("\n[stdout]\n")
        out.write(result.stdout)
        if not result.stdout.endswith("\n"):
            out.write("\n")
    if result.stderr:
        out.write("\n[stderr]\n")
        out.write(result.stderr)
        if not result.stderr.endswith("\n"):
            out.write("\n")
    return out.getvalue()


def _collect_command_manifest(
    output_dir: Path,
    name: str,
    commands: Iterable[tuple[list[str], int]],
    runner: Runner,
) -> None:
    chunks = []
    for argv, timeout in commands:
        chunks.append(_format_result(runner(argv, timeout)))
    _safe_write(output_dir / name, "\n".join(chunks))


def _checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_config_checksums(output_dir: Path, home_root: Path) -> None:
    hermes_home = home_root / ".hermes"
    codex_home = home_root / ".codex"
    candidates: list[Path] = [
        hermes_home / "config.yaml",
        hermes_home / ".env",
        hermes_home / "auth.json",
        hermes_home / "gateway_state.json",
        codex_home / "config.toml",
        codex_home / "auth.json",
        codex_home / "AGENTS.md",
    ]
    for profile_dir in sorted((hermes_home / "profiles").glob("*")):
        candidates.extend(
            [
                profile_dir / "config.yaml",
                profile_dir / ".env",
                profile_dir / "auth.json",
            ]
        )
    unit_dirs = [
        home_root / ".config" / "systemd" / "user",
        Path("/etc/systemd/system"),
        Path("/etc/cron.d"),
    ]
    for directory in unit_dirs:
        if not directory.exists():
            continue
        try:
            for child in sorted(directory.iterdir()):
                name = child.name.lower()
                if any(keyword in name for keyword in SYSTEMD_KEYWORDS) or (
                    directory.name == "cron.d" and name.startswith("hermes")
                ):
                    candidates.append(child)
        except OSError:
            candidates.append(directory)

    lines = ["# sha256 checksums for config/state control files", ""]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not path.exists():
            lines.append(f"MISSING  {path}")
            continue
        if not path.is_file():
            lines.append(f"SKIP     {path}  not a regular file")
            continue
        try:
            lines.append(f"{_checksum_file(path)}  {path}  bytes={path.stat().st_size}")
        except OSError as exc:
            lines.append(f"ERROR    {path}  {exc}")
    _safe_write(output_dir / "config-checksums.txt", "\n".join(lines) + "\n")


def collect_state_db_integrity(output_dir: Path, home_root: Path) -> None:
    db_path = home_root / ".hermes" / "state.db"
    lines = [f"state_db={db_path}"]
    if not db_path.exists():
        lines.append("status=missing")
        _safe_write(output_dir / "state-db-integrity.txt", "\n".join(lines) + "\n")
        return
    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
        lines.append("status=checked")
        for row in rows:
            lines.append(str(row[0]))
    except Exception as exc:
        lines.append("status=error")
        lines.append(f"error={exc}")
    _safe_write(output_dir / "state-db-integrity.txt", "\n".join(lines) + "\n")


def collect_git_status(output_dir: Path, home_root: Path, runner: Runner) -> None:
    stacks = home_root / "stacks"
    repos: list[Path] = []
    if stacks.exists():
        if (stacks / ".git").exists():
            repos.append(stacks)
        try:
            repos.extend(
                child
                for child in sorted(stacks.iterdir())
                if child.is_dir() and (child / ".git").exists()
            )
        except OSError:
            pass
    if REPO_ROOT not in repos and (REPO_ROOT / ".git").exists():
        repos.append(REPO_ROOT)

    chunks = ["# git status summaries", ""]
    for repo in repos:
        chunks.append(f"## {repo}")
        chunks.append(
            _format_result(runner(["git", "-C", str(repo), "status", "--short", "--branch"], 20))
        )
    if not repos:
        chunks.append("No git repositories discovered under stacks.")
    _safe_write(output_dir / "git-status-summary.txt", "\n".join(chunks))


def collect_path_inventory(
    output_dir: Path,
    roots: list[ManagedRoot],
    mount_info: MountInfo,
) -> None:
    payload = {
        "data_mount": asdict(mount_info),
        "managed_roots": [asdict(root) for root in roots],
    }
    _safe_write(
        output_dir / "path-inventory.json",
        json.dumps(payload, indent=2, default=_json_default) + "\n",
    )


def collect_systemd_manifest(output_dir: Path, runner: Runner) -> None:
    _collect_command_manifest(
        output_dir,
        "systemd-units.txt",
        (
            (["systemctl", "--user", "list-units", "--all", "--no-legend", "--no-pager"], 20),
            (["systemctl", "--user", "list-timers", "--all", "--no-legend", "--no-pager"], 20),
            (["systemctl", "--user", "list-unit-files", "--no-legend", "--no-pager"], 20),
            (["systemctl", "list-units", "--all", "--no-legend", "--no-pager"], 20),
            (["systemctl", "list-timers", "--all", "--no-legend", "--no-pager"], 20),
            (["systemctl", "list-unit-files", "--no-legend", "--no-pager"], 20),
        ),
        runner,
    )


def collect_cron_manifest(output_dir: Path, runner: Runner) -> None:
    chunks = [
        _format_result(runner(["crontab", "-l"], 20)),
    ]
    for path in (Path("/etc/crontab"),):
        try:
            chunks.append(f"\n# {path}\n{path.read_text(encoding='utf-8', errors='replace')}")
        except OSError as exc:
            chunks.append(f"\n# {path}\nERROR: {exc}\n")
    cron_d = Path("/etc/cron.d")
    if cron_d.exists():
        try:
            for child in sorted(cron_d.iterdir()):
                if child.is_file():
                    try:
                        text = child.read_text(encoding="utf-8", errors="replace")
                    except OSError as exc:
                        text = f"ERROR: {exc}\n"
                    chunks.append(f"\n# {child}\n{text}")
        except OSError as exc:
            chunks.append(f"\n# {cron_d}\nERROR: {exc}\n")
    _safe_write(output_dir / "cron-jobs.txt", "\n".join(chunks))


def collect_docker_manifest(output_dir: Path, runner: Runner) -> None:
    chunks = [
        _format_result(runner(["docker", "ps", "--format", "{{json .}}"], 30)),
        _format_result(runner(["docker", "volume", "ls"], 30)),
    ]
    volume_ids = runner(["docker", "volume", "ls", "-q"], 30)
    chunks.append(_format_result(volume_ids))
    ids = [line.strip() for line in volume_ids.stdout.splitlines() if line.strip()]
    if ids:
        chunks.append(_format_result(runner(["docker", "volume", "inspect", *ids], 30)))
    else:
        chunks.append("No Docker named volumes returned by `docker volume ls -q`.\n")
    _safe_write(output_dir / "docker-inventory.txt", "\n".join(chunks))


def collect_manifests(
    output_dir: Path,
    options: DryRunOptions,
    roots: list[ManagedRoot],
    mount_info: MountInfo,
    runner: Runner,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    collect_path_inventory(output_dir, roots, mount_info)
    _collect_command_manifest(
        output_dir,
        "lsblk.txt",
        ((["lsblk", "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,UUID"], 20),),
        runner,
    )
    _collect_command_manifest(
        output_dir,
        "findmnt.txt",
        (
            (["findmnt", "-R", str(options.data_mount)], 20),
            (
                [
                    "findmnt",
                    "-T",
                    str(options.data_mount),
                    "-o",
                    "TARGET,SOURCE,FSTYPE,OPTIONS,UUID",
                ],
                20,
            ),
        ),
        runner,
    )
    df_paths = [str(options.data_mount)] + [str(root.source) for root in roots if root.exists]
    _collect_command_manifest(
        output_dir,
        "df.txt",
        ((["df", "-hT", *df_paths], 30),),
        runner,
    )
    du_commands = [
        (["du", "-shx", str(root.source)], options.du_timeout)
        for root in roots
        if root.exists
    ]
    if du_commands:
        _collect_command_manifest(output_dir, "du-summary.txt", du_commands, runner)
    else:
        _safe_write(output_dir / "du-summary.txt", "No managed roots exist on disk.\n")
    collect_config_checksums(output_dir, options.home_root)
    collect_state_db_integrity(output_dir, options.home_root)
    collect_git_status(output_dir, options.home_root, runner)
    collect_systemd_manifest(output_dir, runner)
    collect_cron_manifest(output_dir, runner)
    collect_docker_manifest(output_dir, runner)


def _parse_systemd_units(scope: str, text: str) -> list[SystemdUnit]:
    units: list[SystemdUnit] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        name, _load, active, sub = parts[:4]
        description = parts[4] if len(parts) > 4 else ""
        if any(keyword in name.lower() for keyword in SYSTEMD_KEYWORDS):
            units.append(
                SystemdUnit(
                    scope=scope,
                    name=name,
                    active=active,
                    sub=sub,
                    description=description,
                )
            )
    return units


def discover_systemd_units(runner: Runner) -> list[SystemdUnit]:
    units: list[SystemdUnit] = []
    commands = (
        ("user", ["systemctl", "--user", "list-units", "--all", "--no-legend", "--no-pager"]),
        ("system", ["systemctl", "list-units", "--all", "--no-legend", "--no-pager"]),
    )
    for scope, argv in commands:
        result = runner(argv, 20)
        if result.returncode == 0:
            units.extend(_parse_systemd_units(scope, result.stdout))
    return units


def discover_docker_containers(
    runner: Runner,
    roots: list[ManagedRoot],
) -> tuple[list[DockerContainer], str]:
    ps = runner(["docker", "ps", "-q"], 20)
    if ps.returncode != 0:
        return [], ps.error or ps.stderr.strip() or "docker ps failed"
    ids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not ids:
        return [], ""
    inspected = runner(["docker", "inspect", *ids], 30)
    if inspected.returncode != 0:
        return [], inspected.error or inspected.stderr.strip() or "docker inspect failed"
    try:
        payload = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        return [], f"could not parse docker inspect JSON: {exc}"

    containers: list[DockerContainer] = []
    for item in payload:
        mounts: list[DockerMount] = []
        for mount in item.get("Mounts") or []:
            source = str(mount.get("Source") or "")
            matches = any(_path_is_under(source, root.source) for root in roots if source)
            mounts.append(
                DockerMount(
                    type=str(mount.get("Type") or ""),
                    source=source,
                    destination=str(mount.get("Destination") or ""),
                    name=str(mount.get("Name") or ""),
                    matches_managed_root=matches,
                )
            )
        name = str(item.get("Name") or "").lstrip("/")
        containers.append(
            DockerContainer(
                id=str(item.get("Id") or "")[:12],
                name=name or str(item.get("Id") or "")[:12],
                mounts=mounts,
            )
        )
    return containers, ""


def scan_process_writers(roots: list[ManagedRoot]) -> list[WriterFinding]:
    try:
        import psutil
    except Exception as exc:
        return [WriterFinding(kind="process-scan", detail=f"psutil unavailable: {exc}")]

    findings: list[WriterFinding] = []
    own_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            info = proc.info
            pid = int(info.get("pid") or 0)
            if pid == own_pid:
                continue
            name = str(info.get("name") or "")
            cmdline = " ".join(str(part) for part in (info.get("cmdline") or []))
            needle = f"{name} {cmdline}".lower()
            if not any(keyword in needle for keyword in WRITER_KEYWORDS):
                continue
            paths: list[str] = []
            cwd = info.get("cwd")
            if cwd:
                paths.append(str(cwd))
            try:
                paths.extend(file.path for file in proc.open_files() or [])
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            matched_paths = [
                path
                for path in paths
                if any(_path_is_under(path, root.source) for root in roots)
            ]
            if matched_paths or any(key in needle for key in SYSTEMD_KEYWORDS):
                sample = matched_paths[0] if matched_paths else "command match"
                findings.append(
                    WriterFinding(
                        kind="process",
                        detail=f"pid={pid} name={name} path={sample} cmd={cmdline[:240]}",
                    )
                )
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    return findings


def detect_active_writers(
    roots: list[ManagedRoot],
    runner: Runner,
    process_scanner: ProcessScanner = scan_process_writers,
) -> tuple[list[WriterFinding], list[SystemdUnit], list[DockerContainer], str]:
    findings = process_scanner(roots)
    units = discover_systemd_units(runner)
    for unit in units:
        if unit.active in {"active", "activating", "reloading"}:
            findings.append(
                WriterFinding(
                    kind=f"systemd-{unit.scope}",
                    detail=f"{unit.name} active={unit.active} sub={unit.sub}",
                )
            )
    containers, docker_error = discover_docker_containers(runner, roots)
    for container in containers:
        for mount in container.mounts:
            if mount.type == "bind" and mount.matches_managed_root:
                findings.append(
                    WriterFinding(
                        kind="docker",
                        detail=(
                            f"{container.name} bind-mounts {mount.source} "
                            f"to {mount.destination}"
                        ),
                    )
                )
    return findings, units, containers, docker_error


def _commands_block(commands: Iterable[str]) -> str:
    return "```bash\n" + "\n".join(commands) + "\n```\n"


def _systemctl_prefix(scope: str) -> str:
    return "systemctl --user" if scope == "user" else "sudo systemctl"


def build_command_plan(
    *,
    output_dir: Path,
    options: DryRunOptions,
    roots: list[ManagedRoot],
    mount_info: MountInfo,
    systemd_units: list[SystemdUnit],
    docker_containers: list[DockerContainer],
    writer_findings: list[WriterFinding],
    docker_error: str = "",
) -> str:
    user_units = sorted(
        (unit for unit in systemd_units if unit.scope == "user"),
        key=lambda unit: unit.name,
    )
    system_units = sorted(
        (unit for unit in systemd_units if unit.scope == "system"),
        key=lambda unit: unit.name,
    )
    docker_bind_containers = [
        container
        for container in docker_containers
        if any(
            mount.type == "bind" and mount.matches_managed_root
            for mount in container.mounts
        )
    ]

    out = io.StringIO()
    out.write("# Hermes Data Migration Dry-Run Command Plan\n\n")
    out.write("No commands in this file were executed by the dry-run script.\n\n")
    out.write(f"- manifest_output: `{output_dir}`\n")
    out.write(f"- data_mount: `{options.data_mount}`\n")
    out.write(f"- data_mount_fstype: `{mount_info.fstype}`\n")
    out.write(f"- target_root: `{options.target_root}`\n")
    out.write("- docker_named_volumes: inventory only; no /var/lib/docker move is proposed.\n")
    if writer_findings:
        out.write("- active_writers: allowed only because --allow-active-writers was set.\n")
    out.write("\n")

    out.write("## Managed Paths\n\n")
    out.write("| label | source | target | holdback | bind target | fstab line |\n")
    out.write("| --- | --- | --- | --- | --- | --- |\n")
    for root in roots:
        out.write(
            f"| {root.label} | `{root.source}` | `{root.target}` | "
            f"`{root.holdback}` | `{root.bind_target}` | `{root.fstab_line}` |\n"
        )
    out.write("\n")

    out.write("## 0. Set Migration ID\n\n")
    out.write(
        _commands_block(
            [
                "# Set once during the real migration.",
                'MIGRATION_ID="$(date -u +%Y%m%dT%H%M%SZ)"',
            ]
        )
    )

    out.write("## 1. Pause Writers\n\n")
    pause_commands = [
        "systemctl --user stop hermes-gateway.service  # if present",
        "systemctl --user stop hermes-cron.service  # if present",
        "systemctl --user stop hermes-cron.timer  # if present",
    ]
    for unit in user_units:
        pause_commands.append(f"{_systemctl_prefix(unit.scope)} stop {_quote(unit.name)}")
    for unit in system_units:
        pause_commands.append(f"{_systemctl_prefix(unit.scope)} stop {_quote(unit.name)}")
    for container in docker_bind_containers:
        pause_commands.append(f"docker stop {_quote(container.name or container.id)}")
    pause_commands.append("# Stop Codex/Hadto worker processes after saving their work.")
    out.write(_commands_block(dict.fromkeys(pause_commands)))

    out.write("## 2. Create Btrfs Subvolumes\n\n")
    create_commands = [f"sudo mkdir -p {_quote(options.target_root)}"]
    create_commands.extend(
        f"sudo btrfs subvolume create {_quote(root.target)}" for root in roots
    )
    out.write(_commands_block(create_commands))

    out.write("## 3. Copy Source Roots\n\n")
    copy_commands = []
    for root in roots:
        copy_commands.append(
            "sudo rsync -aHAX --numeric-ids --info=progress2 "
            f"{_quote(str(root.source) + '/')} {_quote(str(root.target) + '/')}"
        )
    out.write(_commands_block(copy_commands))

    out.write("## 4. Rename Originals Into Holdbacks\n\n")
    rename_commands = []
    for root in roots:
        rename_commands.append(f"sudo mv {_quote(root.source)} {root.holdback}")
        rename_commands.append(f"sudo mkdir -p {_quote(root.bind_target)}")
    out.write(_commands_block(rename_commands))

    out.write("## 5. Bind Mounts And Fstab Plan\n\n")
    mount_commands = []
    for root in roots:
        mount_commands.append(f"sudo mount --bind {_quote(root.target)} {_quote(root.bind_target)}")
    mount_commands.append("sudo cp /etc/fstab /etc/fstab.pre-hermes-data-migration")
    for root in roots:
        mount_commands.append(
            "printf '%s\\n' "
            f"{_quote(root.fstab_line)} | sudo tee -a /etc/fstab >/dev/null"
        )
    out.write(_commands_block(mount_commands))

    out.write("## 6. Verification\n\n")
    verify_commands = [
        f"findmnt -T {_quote(root.bind_target)} -o TARGET,SOURCE,FSTYPE,OPTIONS"
        for root in roots
    ]
    verify_commands.extend(
        [
            f"df -hT {_quote(options.data_mount)} "
            + " ".join(_quote(root.bind_target) for root in roots),
            f"sudo btrfs subvolume list {_quote(options.data_mount)}",
            f"python {_quote(REPO_ROOT / 'scripts' / 'data_migration_dry_run.py')} "
            f"--output-dir {_quote(output_dir / 'post-bind-check')} "
            "--allow-active-writers",
        ]
    )
    out.write(_commands_block(verify_commands))

    out.write("## 7. Restart Writers\n\n")
    restart_commands = []
    for unit in system_units:
        restart_commands.append(f"{_systemctl_prefix(unit.scope)} start {_quote(unit.name)}")
    for unit in user_units:
        restart_commands.append(f"{_systemctl_prefix(unit.scope)} start {_quote(unit.name)}")
    restart_commands.extend(
        [
            "systemctl --user start hermes-cron.timer  # if present",
            "systemctl --user start hermes-cron.service  # if present",
            "systemctl --user start hermes-gateway.service  # if present",
        ]
    )
    for container in docker_bind_containers:
        restart_commands.append(f"docker start {_quote(container.name or container.id)}")
    out.write(_commands_block(dict.fromkeys(restart_commands)))

    out.write("## Rollback\n\n")
    rollback_commands = [
        "# Stop writers again before rolling back.",
        "systemctl --user stop hermes-gateway.service  # if present",
        "systemctl --user stop hermes-cron.service  # if present",
        "systemctl --user stop hermes-cron.timer  # if present",
    ]
    for unit in user_units:
        rollback_commands.append(f"{_systemctl_prefix(unit.scope)} stop {_quote(unit.name)}")
    for unit in system_units:
        rollback_commands.append(f"{_systemctl_prefix(unit.scope)} stop {_quote(unit.name)}")
    for container in docker_bind_containers:
        rollback_commands.append(f"docker stop {_quote(container.name or container.id)}")
    for root in reversed(roots):
        rollback_commands.append(f"sudo umount {_quote(root.bind_target)}")
    rollback_commands.append("sudo cp /etc/fstab /etc/fstab.pre-hermes-data-migration-rollback")
    rollback_commands.append("sudoedit /etc/fstab  # remove the exact bind lines listed above")
    for root in roots:
        rollback_commands.append(f"sudo rmdir {_quote(root.bind_target)}")
        rollback_commands.append(f"sudo mv {root.holdback} {_quote(root.source)}")
    rollback_commands.extend(restart_commands)
    rollback_commands.append(
        "# Leave /data/hermes subvolumes intact until application checks pass."
    )
    out.write(_commands_block(dict.fromkeys(rollback_commands)))

    out.write("## Docker Inventory Note\n\n")
    if docker_error:
        out.write(f"Docker inventory error: `{docker_error}`\n\n")
    if docker_containers:
        for container in docker_containers:
            out.write(f"- `{container.name}` `{container.id}`\n")
            for mount in container.mounts:
                marker = "managed-root-bind" if mount.matches_managed_root else "inventory-only"
                out.write(
                    f"  - {marker}: {mount.type} {mount.source or mount.name} -> "
                    f"{mount.destination}\n"
                )
    else:
        out.write("No running Docker containers with inspectable mounts were discovered.\n")
    return out.getvalue()


def _write_refusal(
    output_dir: Path,
    title: str,
    details: Iterable[str],
    stdout: TextIO,
) -> None:
    lines = [title, "", *details]
    text = "\n".join(lines).rstrip() + "\n"
    _safe_write(output_dir / "refusal.txt", text)
    stdout.write(text)
    stdout.write(f"\nManifests written to: {output_dir}\n")


def run_dry_run(
    options: DryRunOptions,
    *,
    runner: Runner | None = None,
    process_scanner: ProcessScanner = scan_process_writers,
    stdout: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    runner = runner or CommandRunner()
    roots = discover_managed_roots(options.home_root, options.target_root)
    mount_info = probe_mount(options.data_mount, runner)

    collect_manifests(options.output_dir, options, roots, mount_info, runner)

    if not mount_info.exists or not mount_info.is_mount or mount_info.fstype != "btrfs":
        details = [
            f"/data path checked: {options.data_mount}",
            f"exists={mount_info.exists}",
            f"is_mount={mount_info.is_mount}",
            f"fstype={mount_info.fstype or '<unknown>'}",
        ]
        if mount_info.error:
            details.append(f"error={mount_info.error}")
        _write_refusal(
            options.output_dir,
            "REFUSAL: /data is absent or is not mounted as btrfs.",
            details,
            stdout,
        )
        return 2

    writers, units, containers, docker_error = detect_active_writers(
        roots,
        runner,
        process_scanner=process_scanner,
    )
    if writers and not options.allow_active_writers:
        _safe_write(
            options.output_dir / "active-writers.json",
            json.dumps([asdict(writer) for writer in writers], indent=2) + "\n",
        )
        _write_refusal(
            options.output_dir,
            "REFUSAL: active writers were detected.",
            [f"- {finding.kind}: {finding.detail}" for finding in writers]
            + [
                "",
                "Re-run only after pausing writers, or use --allow-active-writers "
                "for planning output when you accept the risk.",
            ],
            stdout,
        )
        return 3

    _safe_write(
        options.output_dir / "active-writers.json",
        json.dumps([asdict(writer) for writer in writers], indent=2) + "\n",
    )
    plan = build_command_plan(
        output_dir=options.output_dir,
        options=options,
        roots=roots,
        mount_info=mount_info,
        systemd_units=units,
        docker_containers=containers,
        writer_findings=writers,
        docker_error=docker_error,
    )
    _safe_write(options.output_dir / "command-plan.md", plan)
    stdout.write(plan)
    stdout.write(f"\nManifests written to: {options.output_dir}\n")
    return 0


def _default_output_dir(home_root: Path) -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return home_root / ".hermes" / "data-migration-dry-runs" / stamp


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect preflight manifests and print the no-mutation command plan "
            "for moving Hermes host data roots onto /data/hermes btrfs subvolumes."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Directory for manifests and command-plan.md. Defaults to a timestamped "
            "directory under /home/david/.hermes/data-migration-dry-runs."
        ),
    )
    parser.add_argument(
        "--home-root",
        type=Path,
        default=Path("/home/david"),
        help="Home root containing .hermes, stacks, .ctx-data, and .codex.",
    )
    parser.add_argument(
        "--data-mount",
        type=Path,
        default=Path("/data"),
        help="Mounted btrfs data root. The default and intended host path is /data.",
    )
    parser.add_argument(
        "--target-root",
        type=Path,
        default=Path("/data/hermes"),
        help="Target root for proposed btrfs subvolumes.",
    )
    parser.add_argument(
        "--du-timeout",
        type=int,
        default=120,
        help="Per-root timeout in seconds for du summary collection.",
    )
    parser.add_argument(
        "--allow-active-writers",
        "--skip-writer-check",
        action="store_true",
        dest="allow_active_writers",
        help=(
            "Unsafe: write and print the command plan even when active writers "
            "are detected. Default is to refuse before command-plan emission."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir or _default_output_dir(args.home_root)
    options = DryRunOptions(
        output_dir=output_dir,
        home_root=args.home_root,
        data_mount=args.data_mount,
        target_root=args.target_root,
        allow_active_writers=args.allow_active_writers,
        du_timeout=args.du_timeout,
    )
    return run_dry_run(options)


if __name__ == "__main__":
    raise SystemExit(main())
