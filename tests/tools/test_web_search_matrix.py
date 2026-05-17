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


def test_web_search_matrix_can_compare_registered_providers(monkeypatch):
    web_search_registry._reset_for_tests()
    monkeypatch.setattr(web_tools, "_ensure_web_search_plugins_registered", lambda: None)
    web_search_registry.register_provider(
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
        )
    )
    web_search_registry.register_provider(
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
        )
    )

    try:
        payload = json.loads(
            web_tools.web_search_matrix_tool(
                query="cms dental",
                providers=["parallel", "exa"],
                limit=5,
            )
        )
    finally:
        web_search_registry._reset_for_tests()

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
    web_search_registry._reset_for_tests()
    monkeypatch.setattr(web_tools, "_ensure_web_search_plugins_registered", lambda: None)
    web_search_registry.register_provider(
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
        )
    )
    web_search_registry.register_provider(
        _FakeSearchProvider("firecrawl", error="Payment Required: Insufficient credits")
    )

    try:
        payload = json.loads(
            web_tools.web_search_matrix_tool(
                query="cms dental",
                providers=["firecrawl", "parallel"],
                limit=5,
            )
        )
    finally:
        web_search_registry._reset_for_tests()

    assert payload["success"] is True
    assert payload["provider_results"]["parallel"]["success"] is True
    assert payload["provider_results"]["firecrawl"]["success"] is False
    assert "Insufficient credits" in payload["provider_results"]["firecrawl"]["error"]
