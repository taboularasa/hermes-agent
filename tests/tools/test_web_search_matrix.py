import json

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
