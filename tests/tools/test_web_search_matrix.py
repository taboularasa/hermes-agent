import json

from agent.web_search_provider import WebSearchProvider
from agent import web_search_registry
from tools import web_tools


def test_web_search_matrix_runs_labeled_queries_and_dedupes(monkeypatch):
    def fake_search(query, limit=5):
        return json.dumps(
            {
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": f"{query} result",
                            "url": "https://example.com/shared",
                            "description": "shared",
                            "position": 1,
                        },
                        {
                            "title": f"{query} unique",
                            "url": f"https://example.com/{query.replace(' ', '-')}",
                            "description": "unique",
                            "position": 2,
                        },
                    ]
                },
            }
        )

    monkeypatch.setattr(web_tools, "web_search_tool", fake_search)
    monkeypatch.setattr(web_tools, "_get_search_backend", lambda: "brave-free")

    payload = json.loads(
        web_tools.web_search_matrix_tool(
            [
                {"label": "cms", "query": "cms dental"},
                {"label": "irs", "query": "irs dental"},
            ],
            limit_per_query=2,
        )
    )

    assert payload["success"] is True
    assert payload["meta"]["query_count"] == 2
    assert payload["meta"]["limit_per_query"] == 2
    assert [row["label"] for row in payload["data"]["matrix"]] == ["cms", "irs"]
    urls = [result["url"] for result in payload["data"]["deduped_web"]]
    assert urls.count("https://example.com/shared") == 1
    assert "https://example.com/cms-dental" in urls
    assert "https://example.com/irs-dental" in urls


def test_registered_web_search_matrix_preserves_labeled_query_mode(monkeypatch):
    entry = web_tools.registry.get_entry("web_search_matrix")
    calls = {}

    def fake_matrix_tool(queries, **kwargs):
        calls["queries"] = queries
        calls["kwargs"] = kwargs
        return json.dumps({"success": True})

    monkeypatch.setattr(web_tools, "web_search_matrix_tool", fake_matrix_tool)

    payload = json.loads(
        entry.handler({
            "queries": [{"label": "cms", "query": "cms dental"}],
            "limit_per_query": 2,
        })
    )

    assert payload["success"] is True
    assert calls["queries"] == [{"label": "cms", "query": "cms dental"}]
    assert calls["kwargs"]["limit_per_query"] == 2


class _FakeSearchProvider(WebSearchProvider):
    def __init__(self, name, results=None, error=None):
        self._name = name
        self._results = results or []
        self._error = error

    @property
    def name(self):
        return self._name

    def is_available(self):
        return True

    def search(self, query, limit=5):
        if self._error:
            return {"success": False, "error": self._error}
        return {
            "success": True,
            "data": {"web": self._results[:limit]},
        }


def _install_fake_search_providers(monkeypatch, providers):
    providers_by_name = {provider.name: provider for provider in providers}
    monkeypatch.setattr(web_tools, "_ensure_web_search_plugins_registered", lambda: None)
    monkeypatch.setattr(
        web_search_registry,
        "list_providers",
        lambda: list(providers_by_name.values()),
    )
    monkeypatch.setattr(
        web_search_registry,
        "get_provider",
        lambda name: providers_by_name.get(str(name).strip()),
    )


def test_web_search_matrix_can_compare_registered_providers(monkeypatch):
    _install_fake_search_providers(
        monkeypatch,
        [
            _FakeSearchProvider(
                "parallel",
                [
                    {
                        "title": "Shared",
                        "url": "https://example.com/shared/",
                        "description": "from parallel",
                        "position": 1,
                    }
                ],
            ),
            _FakeSearchProvider(
                "exa",
                [
                    {
                        "title": "Shared exa",
                        "url": "https://example.com/shared",
                        "description": "from exa",
                        "position": 2,
                    },
                    {
                        "title": "Unique",
                        "url": "https://example.com/unique",
                        "description": "from exa",
                        "position": 1,
                    },
                ],
            ),
        ],
    )

    payload = json.loads(
        web_tools.web_search_matrix_tool(
            query="cms dental",
            providers=["parallel", "exa"],
            limit=5,
        )
    )

    assert payload["success"] is True
    assert payload["strategy"] == "provider_matrix"
    assert payload["providers_used"] == ["parallel", "exa"]
    shared = next(
        result
        for result in payload["data"]["web"]
        if result["url"] == "https://example.com/shared/"
    )
    assert shared["provider_hits"] == 2
    assert shared["providers"] == ["exa", "parallel"]
    assert shared["positions"] == {"parallel": 1, "exa": 2}


def test_web_search_matrix_keeps_provider_failures_visible(monkeypatch):
    _install_fake_search_providers(
        monkeypatch,
        [
            _FakeSearchProvider(
                "parallel",
                [
                    {
                        "title": "Result",
                        "url": "https://example.com/result",
                        "description": "ok",
                        "position": 1,
                    }
                ],
            ),
            _FakeSearchProvider(
                "firecrawl",
                error="Payment Required: Insufficient credits",
            ),
        ],
    )

    payload = json.loads(
        web_tools.web_search_matrix_tool(
            query="cms dental",
            providers=["firecrawl", "parallel"],
            limit=5,
        )
    )

    assert payload["success"] is True
    assert payload["provider_results"]["parallel"]["success"] is True
    assert payload["provider_results"]["firecrawl"]["success"] is False
    assert "Insufficient credits" in payload["provider_results"]["firecrawl"]["error"]
