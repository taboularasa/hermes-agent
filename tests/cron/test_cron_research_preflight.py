import json
from unittest.mock import MagicMock, patch

from cron import scheduler


_WEB_ENV_VARS = [
    "EXA_API_KEY",
    "PARALLEL_API_KEY",
    "TAVILY_API_KEY",
    "FIRECRAWL_API_KEY",
    "FIRECRAWL_API_URL",
    "FIRECRAWL_GATEWAY_URL",
    "TOOL_GATEWAY_DOMAIN",
    "TOOL_GATEWAY_SCHEME",
    "TOOL_GATEWAY_USER_TOKEN",
    "SEARXNG_URL",
    "BRAVE_SEARCH_API_KEY",
]


def _clear_web_env(monkeypatch):
    for name in _WEB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _check(report, kind, name):
    for item in report["checks"]:
        if item["kind"] == kind and item["name"] == name:
            return item
    raise AssertionError(f"missing preflight check for {kind}:{name}")


def test_web_search_matrix_is_exposed_when_web_backend_available(monkeypatch):
    import tools.web_tools as web_tools
    from model_tools import get_tool_definitions
    from tools.registry import invalidate_check_fn_cache

    _clear_web_env(monkeypatch)
    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {})
    monkeypatch.setattr(web_tools, "_is_backend_available", lambda backend: backend == "brave-free")
    invalidate_check_fn_cache()

    names = {
        tool["function"]["name"]
        for tool in get_tool_definitions(["web"], [], quiet_mode=True)
    }

    assert "web_search_matrix" in names


def test_cron_preflight_distinguishes_missing_web_credentials(monkeypatch):
    import tools.web_tools as web_tools
    from tools.registry import invalidate_check_fn_cache

    _clear_web_env(monkeypatch)
    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {})
    monkeypatch.setattr(web_tools, "_is_backend_available", lambda backend: False)
    monkeypatch.setattr(
        "plugins.web.firecrawl.provider.check_firecrawl_api_key",
        lambda: False,
    )
    invalidate_check_fn_cache()

    prompt = "Run ontology research with web_search_matrix and Firecrawl."
    report = scheduler._build_cron_preflight_report(
        {"id": "job1", "prompt": prompt},
        prompt,
        ["web"],
        [],
    )

    matrix = _check(report, "tool", "web_search_matrix")
    firecrawl = _check(report, "web_backend", "firecrawl")

    assert matrix["status"] == "available"
    assert matrix["category"] == "available"
    assert firecrawl["status"] == "unavailable"
    assert firecrawl["category"] == "provider_credentials_absent"

    markdown = scheduler._format_cron_preflight_markdown(report)
    assert "tool `web_search_matrix`: available (available)" in markdown
    assert "web backend `firecrawl`: unavailable (provider_credentials_absent)" in markdown
    assert "downgrade:" in markdown


def test_cron_preflight_distinguishes_tool_surface_absence(monkeypatch):
    import tools.web_tools as web_tools
    from tools.registry import invalidate_check_fn_cache

    _clear_web_env(monkeypatch)
    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {})
    monkeypatch.setattr(web_tools, "_is_backend_available", lambda backend: True)
    invalidate_check_fn_cache()

    report = scheduler._build_cron_preflight_report(
        {
            "id": "job2",
            "prompt": "Run ontology research.",
            "required_tools": ["web_search_matrix"],
        },
        "Run ontology research.",
        ["terminal"],
        [],
    )

    matrix = _check(report, "tool", "web_search_matrix")

    assert matrix["status"] == "unavailable"
    assert matrix["category"] == "tool_surface_absent"
    assert "not selected" in matrix["detail"]


def test_run_job_includes_preflight_in_prompt_and_report(monkeypatch):
    import tools.web_tools as web_tools
    from tools.registry import invalidate_check_fn_cache

    _clear_web_env(monkeypatch)
    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {})
    monkeypatch.setattr(web_tools, "_is_backend_available", lambda backend: False)
    monkeypatch.setattr(
        "plugins.web.firecrawl.provider.check_firecrawl_api_key",
        lambda: False,
    )
    invalidate_check_fn_cache()

    agent = MagicMock()
    agent.run_conversation = MagicMock(return_value={"final_response": "ok", "messages": []})
    fake_runtime = {
        "provider": "openrouter",
        "api_mode": "chat_completions",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "test-key",
        "source": "stub",
        "requested_provider": None,
    }
    session_db = MagicMock()
    job = {
        "id": "preflightjob",
        "name": "ontology research",
        "prompt": "Use web_search_matrix and Firecrawl for ontology research.",
        "schedule": {"kind": "once"},
        "schedule_display": "manual",
    }

    with patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value=fake_runtime), \
         patch("tools.mcp_tool.discover_mcp_tools", return_value=[]), \
         patch("hermes_state.SessionDB", return_value=session_db), \
         patch("run_agent.AIAgent", return_value=agent):
        success, output, final_response, error = scheduler.run_job(job)

    assert success is True
    assert final_response == "ok"
    assert error is None
    assert "## Cron Preflight" in output
    assert "tool `web_search_matrix`: available (available)" in output
    assert "web backend `firecrawl`: unavailable (provider_credentials_absent)" in output

    prompt_arg = agent.run_conversation.call_args.args[0]
    assert prompt_arg.startswith("## Cron Preflight")
    assert "Treat this preflight as authoritative" in prompt_arg


def test_ontology_degradation_report_classifies_runtime_failures():
    job = {
        "id": "ontology-job",
        "name": "ontology research",
        "prompt": "Run ontology research with web_extract and Firecrawl.",
    }
    result = {
        "messages": [
            {
                "role": "tool",
                "content": json.dumps(
                    {
                        "results": [
                            {
                                "url": "https://www.medicaid.gov/example.pdf",
                                "error": "Payment Required: Insufficient credits",
                            }
                        ],
                        "meta": {
                            "degradations": [
                                {
                                    "category": "provider_credit_exhaustion",
                                    "primary_provider": "firecrawl",
                                }
                            ]
                        },
                    }
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Blob-store publication failed because docker: command not found; "
                    "manifest recorded blob_store: null. "
                    "/home/david/.hermes/notes/ontology-research-cycle/2026.md "
                    "was read-only, so wrote research/notes/ontology-research-cycle/2026.md."
                ),
            },
        ]
    }

    report = scheduler._build_cron_degradation_report(
        job,
        job["prompt"],
        result,
        "completed with degraded publication",
    )
    categories = {check["category"] for check in report["checks"]}

    assert report["has_degradations"] is True
    assert "provider_credit_exhaustion" in categories
    assert "docker_unavailable" in categories
    assert "blob_publication_deferred" in categories
    assert "notes_read_only" in categories

    markdown = scheduler._format_cron_degradation_markdown(report)
    assert "## Cron Degradation Classification" in markdown
    assert '"category": "provider_credit_exhaustion"' in markdown
