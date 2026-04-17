"""Tests for SSH skill and credential sync."""

import subprocess
from unittest.mock import patch

from tools.environments import ssh as ssh_env
from tools.environments.ssh import SSHEnvironment


def _make_env(monkeypatch, *, port=22, key_path=""):
    monkeypatch.setattr(ssh_env.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(
        ssh_env.SSHEnvironment, "_establish_connection", lambda self: None
    )
    monkeypatch.setattr(
        ssh_env.SSHEnvironment,
        "_detect_remote_home",
        lambda self: "/home/testuser",
    )
    original_sync = ssh_env.SSHEnvironment._sync_skills_and_credentials
    monkeypatch.setattr(
        ssh_env.SSHEnvironment,
        "_sync_skills_and_credentials",
        lambda self: None,
    )
    env = SSHEnvironment(
        host="example.com",
        user="testuser",
        port=port,
        key_path=key_path,
    )
    monkeypatch.setattr(ssh_env.SSHEnvironment, "_sync_skills_and_credentials", original_sync)
    return env


def test_sync_no_mounts_is_noop(monkeypatch):
    env = _make_env(monkeypatch)
    monkeypatch.setattr(
        "tools.credential_files.get_credential_file_mounts", lambda: []
    )
    monkeypatch.setattr(
        "tools.credential_files.get_skills_directory_mount",
        lambda container_base=None: None,
    )

    with patch.object(subprocess, "run") as mock_run:
        env._sync_skills_and_credentials()

    mock_run.assert_not_called()


def test_sync_remaps_remote_paths_and_rsyncs_credentials_and_skills(monkeypatch):
    env = _make_env(monkeypatch)
    monkeypatch.setattr(
        "tools.credential_files.get_credential_file_mounts",
        lambda: [
            {
                "host_path": "/tmp/token.txt",
                "container_path": "/root/.hermes/credentials/token.txt",
            }
        ],
    )
    monkeypatch.setattr(
        "tools.credential_files.get_skills_directory_mount",
        lambda container_base=None: {
            "host_path": "/tmp/skills",
            "container_path": f"{container_base}/skills",
        },
    )

    run_calls = []

    def capture_run(cmd, **kwargs):
        run_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=capture_run):
        env._sync_skills_and_credentials()

    assert len(run_calls) == 4

    mkdir_cred_cmd = run_calls[0]
    assert mkdir_cred_cmd[-1] == "mkdir -p /home/testuser/.hermes/credentials"

    rsync_cred_cmd = run_calls[1]
    assert rsync_cred_cmd[0] == "rsync"
    assert rsync_cred_cmd[-2] == "/tmp/token.txt"
    assert rsync_cred_cmd[-1] == (
        "testuser@example.com:/home/testuser/.hermes/credentials/token.txt"
    )

    mkdir_skills_cmd = run_calls[2]
    assert mkdir_skills_cmd[-1] == "mkdir -p /home/testuser/.hermes/skills"

    rsync_skills_cmd = run_calls[3]
    assert rsync_skills_cmd[0] == "rsync"
    assert rsync_skills_cmd[-2] == "/tmp/skills/"
    assert rsync_skills_cmd[-1] == "testuser@example.com:/home/testuser/.hermes/skills/"


def test_sync_includes_custom_port_and_key(monkeypatch):
    env = _make_env(monkeypatch, port=2222, key_path="/tmp/test-key")
    monkeypatch.setattr(
        "tools.credential_files.get_credential_file_mounts",
        lambda: [
            {
                "host_path": "/tmp/token.txt",
                "container_path": "/root/.hermes/credentials/token.txt",
            }
        ],
    )
    monkeypatch.setattr(
        "tools.credential_files.get_skills_directory_mount",
        lambda container_base=None: None,
    )

    run_calls = []

    def capture_run(cmd, **kwargs):
        run_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=capture_run):
        env._sync_skills_and_credentials()

    assert len(run_calls) == 2
    mkdir_cmd = run_calls[0]
    assert "-p" in mkdir_cmd and "2222" in mkdir_cmd
    assert "-i" in mkdir_cmd and "/tmp/test-key" in mkdir_cmd

    rsync_cmd = run_calls[1]
    assert "-e" in rsync_cmd
    ssh_opts = rsync_cmd[rsync_cmd.index("-e") + 1]
    assert "-p 2222" in ssh_opts
    assert "-i /tmp/test-key" in ssh_opts


def test_sync_failures_are_swallowed(monkeypatch):
    env = _make_env(monkeypatch)
    monkeypatch.setattr(
        "tools.credential_files.get_credential_file_mounts",
        lambda: [
            {
                "host_path": "/tmp/token.txt",
                "container_path": "/root/.hermes/credentials/token.txt",
            }
        ],
    )
    monkeypatch.setattr(
        "tools.credential_files.get_skills_directory_mount",
        lambda container_base=None: None,
    )

    with patch.object(subprocess, "run", side_effect=OSError("boom")):
        env._sync_skills_and_credentials()
