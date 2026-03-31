"""Tests for the /apps gateway slash command and host app discovery."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.host_apps import HostApp, discover_host_apps, format_host_apps_markdown
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/apps", platform=Platform.SLACK, user_id="U1", chat_id="C1"):
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_db = None
    runner.session_store = MagicMock()
    return runner


class TestHostAppDiscovery:
    def test_discover_host_apps_combines_docker_and_process_uis(self, tmp_path: Path):
        for name, remote in {
            "hadto-pipeline": "https://github.com/taboularasa/hadto-pipeline.git",
            "hadto-ontology-workbench": "https://github.com/taboularasa/hadto-ontology-workbench.git",
            "ontology-explainer": "git@github.com:taboularasa/ontology-explainer.git",
            "smb-ontology-platform": "https://github.com/taboularasa/smb-ontology-platform.git",
        }.items():
            repo = tmp_path / name
            (repo / ".git").mkdir(parents=True)
            (repo / ".git" / "config").write_text(
                f'[remote "origin"]\n    url = {remote}\n',
                encoding="utf-8",
            )
            if name == "ontology-explainer":
                (repo / "app").mkdir(parents=True)
                (repo / "app" / "layout.tsx").write_text(
                    "export const metadata = { title: 'Multi-Layer Ontology Diagram', description: 'Systems-style diagram visualizing Software, SOP, and Training ontologies with cross-layer constraints' }",
                    encoding="utf-8",
                )
            else:
                (repo / "README.md").write_text(f"# {name}\n\nDescription for {name}.\n", encoding="utf-8")

        command_outputs = {
            ("tailscale", "ip", "-4"): "100.72.243.76\n",
            ("tailscale", "status", "--json"):
                '{"Self":{"DNSName":"hadto.tailcad088.ts.net."}}',
            ("tailscale", "serve", "status", "--json"):
                '{"Web":{"hadto.tailcad088.ts.net:443":{"Handlers":{"/graphs":{"Proxy":"http://127.0.0.1:7878"}}}}}',
            ("docker", "ps", "--format", "{{json .}}"):
                '{"Names":"hadto-pipeline","Ports":"127.0.0.1:5100->5000/tcp"}\n'
                '{"Names":"hadto-ontology-workbench","Ports":"100.72.243.76:3020->3000/tcp"}\n'
                '{"Names":"ontology-triplestore","Ports":"127.0.0.1:7878->7878/tcp"}\n'
                '{"Names":"ontology-api","Ports":"8000/tcp"}\n',
            ("ss", "-H", "-ltnp"):
                'LISTEN 0 511 127.0.0.1:3017 0.0.0.0:* users:(("next-server",pid=4242,fd=22))\n',
        }

        def fake_run(command):
            return command_outputs.get(tuple(command), "")

        with patch("gateway.host_apps._run_text", side_effect=fake_run), \
             patch("gateway.host_apps.os.readlink", return_value=str(tmp_path / "ontology-explainer")):
            apps = discover_host_apps(tmp_path)

        links = {app.title: app.link for app in apps}
        assert links["Hadto Pipeline"] == "http://127.0.0.1:5100"
        assert links["Hadto Ontology Workbench"] == "http://hadto.tailcad088.ts.net:3020"
        assert links["Oxigraph Triplestore"] == "https://hadto.tailcad088.ts.net/graphs"
        assert links["Ontology Explainer"] == "http://127.0.0.1:3017"

        repo_urls = {app.title: app.repo_url for app in apps}
        assert repo_urls["Oxigraph Triplestore"] == "https://github.com/taboularasa/smb-ontology-platform"
        assert repo_urls["Ontology Explainer"] == "https://github.com/taboularasa/ontology-explainer"

    def test_format_host_apps_markdown(self):
        markdown = format_host_apps_markdown([
            HostApp(
                title="Hadto Pipeline",
                description="Venture scoring dashboard.",
                link="http://127.0.0.1:5100",
                repo_url="https://github.com/taboularasa/hadto-pipeline",
            )
        ])
        assert "Running dashboards" in markdown
        assert "Hadto Pipeline" in markdown
        assert "https://github.com/taboularasa/hadto-pipeline" in markdown


class TestAppsCommand:
    @pytest.mark.asyncio
    async def test_handle_apps_command_returns_formatted_inventory(self):
        runner = _make_runner()
        event = _make_event()
        fake_apps = []

        with patch("gateway.host_apps.discover_host_apps", return_value=fake_apps), \
             patch("gateway.host_apps.format_host_apps_markdown", return_value="inventory output"):
            result = await runner._handle_apps_command(event)

        assert result == "inventory output"

    def test_apps_in_registry_and_slack_mapping(self):
        from hermes_cli.commands import COMMANDS, resolve_command, slack_subcommand_map

        assert resolve_command("apps").name == "apps"
        assert resolve_command("dashboards").name == "apps"
        assert "/apps" not in COMMANDS  # gateway-only commands are omitted from generic help dict
        assert slack_subcommand_map()["apps"] == "/apps"
