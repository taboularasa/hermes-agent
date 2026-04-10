import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermes_cli.ctx_runtime import CtxBinding


def test_cli_main_skips_hermes_worktree_when_ctx_available(monkeypatch):
    prompt_toolkit_stubs = {
        "prompt_toolkit": MagicMock(),
        "prompt_toolkit.history": MagicMock(),
        "prompt_toolkit.styles": MagicMock(),
        "prompt_toolkit.patch_stdout": MagicMock(),
        "prompt_toolkit.application": MagicMock(),
        "prompt_toolkit.layout": MagicMock(),
        "prompt_toolkit.layout.processors": MagicMock(),
        "prompt_toolkit.filters": MagicMock(),
        "prompt_toolkit.layout.dimension": MagicMock(),
        "prompt_toolkit.layout.menus": MagicMock(),
        "prompt_toolkit.widgets": MagicMock(),
        "prompt_toolkit.key_binding": MagicMock(),
        "prompt_toolkit.completion": MagicMock(),
        "prompt_toolkit.formatted_text": MagicMock(),
        "prompt_toolkit.auto_suggest": MagicMock(),
    }

    with patch.dict(sys.modules, prompt_toolkit_stubs):
        import cli as cli_mod

        cli_mod = importlib.reload(cli_mod)
        fake_cli = SimpleNamespace(
            session_id="sess-1",
            system_prompt="",
            preloaded_skills=[],
            run=lambda: None,
        )

        monkeypatch.setattr(cli_mod, "HermesCLI", lambda **_kwargs: fake_cli)
        monkeypatch.setattr(cli_mod, "_parse_skills_argument", lambda _skills: [])

        setup_calls = []
        monkeypatch.setattr(cli_mod, "_setup_worktree", lambda *_args, **_kwargs: setup_calls.append(True))
        monkeypatch.setattr(
            sys.modules["hermes_cli.ctx_runtime"],
            "maybe_bind_ctx_session",
            lambda **_kwargs: CtxBinding(
                active=True,
                reason="ctx workspace available",
                session_id="sess-1",
                platform="cli",
                workspace_id="ws-1",
            ),
        )

        cli_mod.main(worktree=True)
        assert setup_calls == []
