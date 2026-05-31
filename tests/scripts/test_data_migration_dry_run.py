from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_data_migration_dry_run_under_test",
        Path(__file__).resolve().parents[2] / "scripts" / "data_migration_dry_run.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(
        self,
        module,
        *,
        fstype: str = "btrfs",
        systemd_stdout: str = "",
        docker_inspect: list[dict] | None = None,
        docker_volumes: tuple[str, ...] = (),
    ) -> None:
        self.module = module
        self.fstype = fstype
        self.systemd_stdout = systemd_stdout
        self.docker_inspect = docker_inspect or []
        self.docker_volumes = docker_volumes
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: int = 30):
        self.calls.append(argv)
        if argv[:2] == ["findmnt", "-J"]:
            payload = {
                "filesystems": [
                    {
                        "target": argv[3],
                        "source": "/dev/nvme-test",
                        "fstype": self.fstype,
                        "options": "rw,relatime",
                        "uuid": "test-uuid",
                    }
                ]
            }
            return self.module.RunResult(argv, 0, json.dumps(payload), "")
        if argv[0] == "systemctl" and "list-units" in argv:
            return self.module.RunResult(argv, 0, self.systemd_stdout, "")
        if argv[:3] == ["docker", "ps", "-q"]:
            ids = [item.get("Id", "")[:12] for item in self.docker_inspect]
            return self.module.RunResult(argv, 0, "\n".join(ids) + ("\n" if ids else ""), "")
        if argv[:2] == ["docker", "inspect"]:
            return self.module.RunResult(argv, 0, json.dumps(self.docker_inspect), "")
        if argv[:3] == ["docker", "volume", "ls"] and "-q" in argv:
            return self.module.RunResult(argv, 0, "\n".join(self.docker_volumes), "")
        return self.module.RunResult(argv, 0, "ok\n", "")


def _make_host_tree(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home" / "david"
    data = tmp_path / "data"
    target = data / "hermes"
    output = tmp_path / "dry-run"
    for rel in (".hermes", "stacks", ".ctx-data", ".codex"):
        (home / rel).mkdir(parents=True)
    data.mkdir()
    (home / ".hermes" / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    return home, data, target, output


def test_managed_roots_enumerate_required_paths():
    module = _load_module()

    roots = module.discover_managed_roots(Path("/home/david"), Path("/data/hermes"))

    assert [root.label for root in roots] == [
        "profile-default",
        "stacks",
        "ctx-data",
        "codex-home",
    ]
    hermes = roots[0]
    assert hermes.source == Path("/home/david/.hermes")
    assert hermes.target == Path("/data/hermes/profile-default")
    assert (
        hermes.holdback
        == "/home/david/.hermes.pre-data-migration-holdback.${MIGRATION_ID}"
    )
    assert hermes.bind_target == Path("/home/david/.hermes")
    assert (
        hermes.fstab_line
        == "/data/hermes/profile-default /home/david/.hermes none "
        "bind,x-systemd.requires-mounts-for=/data 0 0"
    )


def test_command_plan_contains_required_commands_and_rollback(tmp_path):
    module = _load_module()
    roots = module.discover_managed_roots(Path("/home/david"), Path("/data/hermes"))
    options = module.DryRunOptions(
        output_dir=tmp_path / "out",
        home_root=Path("/home/david"),
        data_mount=Path("/data"),
        target_root=Path("/data/hermes"),
    )
    container = module.DockerContainer(
        id="abc123",
        name="code-worker",
        mounts=[
            module.DockerMount(
                type="bind",
                source="/home/david/stacks/hermes-agent",
                destination="/workspace",
                matches_managed_root=True,
            )
        ],
    )

    plan = module.build_command_plan(
        output_dir=options.output_dir,
        options=options,
        roots=roots,
        mount_info=module.MountInfo(Path("/data"), True, True, fstype="btrfs"),
        systemd_units=[
            module.SystemdUnit("user", "hermes-gateway.service", "active", "running"),
            module.SystemdUnit("user", "hermes-cron.timer", "active", "waiting"),
        ],
        docker_containers=[container],
        writer_findings=[],
    )

    assert "sudo btrfs subvolume create /data/hermes/profile-default" in plan
    assert "sudo rsync -aHAX --numeric-ids --info=progress2" in plan
    assert "/home/david/.hermes.pre-data-migration-holdback.${MIGRATION_ID}" in plan
    assert "sudo mount --bind /data/hermes/profile-default /home/david/.hermes" in plan
    assert (
        "/data/hermes/profile-default /home/david/.hermes none "
        "bind,x-systemd.requires-mounts-for=/data 0 0"
    ) in plan
    assert "systemctl --user stop hermes-gateway.service" in plan
    assert "docker stop code-worker" in plan
    assert "## Rollback" in plan
    assert "sudo umount /home/david/.codex" in plan
    assert 'MIGRATION_ID="$(date -u +%Y%m%dT%H%M%SZ)"' in plan
    assert "sudo mv /home/david/.codex.pre-data-migration-holdback.${MIGRATION_ID}" in plan
    assert "docker_named_volumes: inventory only" in plan
    assert "no /var/lib/docker move is proposed" in plan


def test_run_refuses_non_btrfs_before_plan(tmp_path):
    module = _load_module()
    home, data, target, output = _make_host_tree(tmp_path)
    options = module.DryRunOptions(
        output_dir=output,
        home_root=home,
        data_mount=data,
        target_root=target,
    )
    stdout = io.StringIO()

    code = module.run_dry_run(
        options,
        runner=FakeRunner(module, fstype="ext4"),
        process_scanner=lambda roots: [],
        stdout=stdout,
    )

    assert code == 2
    assert "REFUSAL: /data is absent or is not mounted as btrfs." in stdout.getvalue()
    assert (output / "path-inventory.json").exists()
    assert (output / "refusal.txt").exists()
    assert not (output / "command-plan.md").exists()


def test_run_refuses_active_writers_by_default(tmp_path):
    module = _load_module()
    home, data, target, output = _make_host_tree(tmp_path)
    options = module.DryRunOptions(
        output_dir=output,
        home_root=home,
        data_mount=data,
        target_root=target,
    )
    stdout = io.StringIO()

    code = module.run_dry_run(
        options,
        runner=FakeRunner(module),
        process_scanner=lambda roots: [
            module.WriterFinding("process", "pid=123 path=/home/david/stacks")
        ],
        stdout=stdout,
    )

    assert code == 3
    assert "REFUSAL: active writers were detected." in stdout.getvalue()
    assert (output / "active-writers.json").exists()
    assert not (output / "command-plan.md").exists()


def test_run_writes_manifests_and_plan_when_active_writers_allowed(tmp_path):
    module = _load_module()
    home, data, target, output = _make_host_tree(tmp_path)
    options = module.DryRunOptions(
        output_dir=output,
        home_root=home,
        data_mount=data,
        target_root=target,
        allow_active_writers=True,
    )
    docker_inspect = [
        {
            "Id": "abcdef1234567890",
            "Name": "/hermes-worker",
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(home / "stacks" / "hermes-agent"),
                    "Destination": "/workspace",
                }
            ],
        }
    ]
    stdout = io.StringIO()

    code = module.run_dry_run(
        options,
        runner=FakeRunner(
            module,
            systemd_stdout=(
                "hermes-gateway.service loaded active running Hermes Gateway\n"
            ),
            docker_inspect=docker_inspect,
            docker_volumes=("hermes-cache",),
        ),
        process_scanner=lambda roots: [
            module.WriterFinding("process", f"pid=123 path={home / 'stacks'}")
        ],
        stdout=stdout,
    )

    assert code == 0
    plan = (output / "command-plan.md").read_text(encoding="utf-8")
    assert "active_writers: allowed only because --allow-active-writers was set" in plan
    assert "docker stop hermes-worker" in plan
    assert (output / "lsblk.txt").exists()
    assert (output / "findmnt.txt").exists()
    assert (output / "df.txt").exists()
    assert (output / "du-summary.txt").exists()
    assert (output / "config-checksums.txt").exists()
    assert (output / "state-db-integrity.txt").exists()
    assert (output / "git-status-summary.txt").exists()
    assert (output / "docker-inventory.txt").exists()
