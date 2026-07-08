from agent.self_improvement_planning_surface import (
    build_linear_planning_surface,
    format_linear_planning_surface_summary,
)


def test_linear_planning_surface_names_missing_fields_and_candidates():
    surface = build_linear_planning_surface(
        [
            {
                "id": "HAD-273",
                "title": "Harden the Linear self-improvement planning surface",
                "repo": "taboularasa/hermes-agent",
                "lane": "Maintenance",
            },
            {
                "id": "HAD-274",
                "title": "Complete planning fixture",
                "repo": "taboularasa/hermes-agent",
                "lane": "Maintenance",
                "verification": "Run the focused self-improvement tests.",
                "activeStatusComment": "Branch is under native fallback implementation.",
            },
        ]
    )

    assert surface["surface"] == "linear_planning_surface"
    assert surface["status"] == "partial"
    assert surface["score"] == 0.6667
    assert surface["missing_field_counts"] == {
        "active_status_comment": 1,
        "verification": 1,
    }
    assert surface["issue_samples"] == [
        {
            "candidate_id": "HAD-273",
            "title": "Harden the Linear self-improvement planning surface",
            "missing_fields": ["active_status_comment", "verification"],
            "expected_aliases": {
                "active_status_comment": [
                    "active_status_comment",
                    "activeStatusComment",
                    "latest_status_comment",
                    "latestStatusComment",
                    "status_comment",
                    "statusComment",
                    "status_comments",
                    "statusComments",
                    "comments",
                ],
                "verification": [
                    "verification",
                    "verification_expectation",
                    "verificationExpectation",
                    "verification_plan",
                    "verificationPlan",
                    "verification_targets",
                    "verificationTargets",
                ],
            },
        }
    ]
    assert surface["missing_field_details"] == [
        {
            "field": "active_status_comment",
            "expected_aliases": [
                "active_status_comment",
                "activeStatusComment",
                "latest_status_comment",
                "latestStatusComment",
                "status_comment",
                "statusComment",
                "status_comments",
                "statusComments",
                "comments",
            ],
            "missing_count": 1,
            "sample_candidates": [
                {
                    "candidate_id": "HAD-273",
                    "title": "Harden the Linear self-improvement planning surface",
                }
            ],
        },
        {
            "field": "verification",
            "expected_aliases": [
                "verification",
                "verification_expectation",
                "verificationExpectation",
                "verification_plan",
                "verificationPlan",
                "verification_targets",
                "verificationTargets",
            ],
            "missing_count": 1,
            "sample_candidates": [
                {
                    "candidate_id": "HAD-273",
                    "title": "Harden the Linear self-improvement planning surface",
                }
            ],
        },
    ]
    assert surface["actionable_causes"] == [
        {
            "cause": "missing_linear_planning_field",
            "field": "active_status_comment",
            "missing_count": 1,
            "sample_candidate_ids": ["HAD-273"],
            "accepted_aliases": [
                "active_status_comment",
                "activeStatusComment",
                "latest_status_comment",
                "latestStatusComment",
                "status_comment",
                "statusComment",
                "status_comments",
                "statusComments",
                "comments",
            ],
            "action": (
                "Populate one of active_status_comment, activeStatusComment, "
                "latest_status_comment, latestStatusComment, status_comment, "
                "statusComment, status_comments, statusComments, comments on "
                "the Linear backlog candidate metadata."
            ),
        },
        {
            "cause": "missing_linear_planning_field",
            "field": "verification",
            "missing_count": 1,
            "sample_candidate_ids": ["HAD-273"],
            "accepted_aliases": [
                "verification",
                "verification_expectation",
                "verificationExpectation",
                "verification_plan",
                "verificationPlan",
                "verification_targets",
                "verificationTargets",
            ],
            "action": (
                "Populate one of verification, verification_expectation, "
                "verificationExpectation, verification_plan, verificationPlan, "
                "verification_targets, verificationTargets on the Linear backlog "
                "candidate metadata."
            ),
        },
    ]
    assert (
        format_linear_planning_surface_summary(surface)
        == "- linear_planning_surface_missing=active_status_comment=1 (HAD-273), "
        "verification=1 (HAD-273)"
    )
    synthetic_output = {
        "capacity": {"linear_planning_surface": surface},
        "summary_markdown": format_linear_planning_surface_summary(surface),
    }
    assert synthetic_output["capacity"]["linear_planning_surface"]["actionable_causes"][
        0
    ]["field"] == "active_status_comment"
    assert "active_status_comment=1 (HAD-273)" in synthetic_output["summary_markdown"]


def test_linear_planning_surface_bounds_missing_field_diagnostics():
    surface = build_linear_planning_surface(
        [
            {
                "id": f"HAD-27{idx}",
                "title": f"Planning candidate {idx}",
                "lane": "Maintenance",
            }
            for idx in range(6)
        ]
    )

    assert surface["status"] == "partial"
    assert surface["missing_field_counts"] == {
        "active_status_comment": 6,
        "verification": 6,
    }
    assert surface["issue_sample_limit"] == 5
    assert len(surface["issue_samples"]) == 5

    details = {
        detail["field"]: detail for detail in surface["missing_field_details"]
    }
    assert details["verification"]["missing_count"] == 6
    assert len(details["verification"]["sample_candidates"]) == 5


def test_linear_planning_surface_accepts_nested_alias_values():
    surface = build_linear_planning_surface(
        {
            "backlog_candidates": [
                {
                    "activeLinearIssueIds": ["HAD-300"],
                    "summary": "Nested metadata candidate",
                    "team": {"name": "Maintenance"},
                    "verificationPlan": {"body": "Run benchmark detail tests."},
                    "comments": [{"body": "Pipeline output has exact missing fields."}],
                }
            ]
        }
    )

    assert surface["status"] == "pass"
    assert surface["score"] == 1.0
    assert surface["missing_field_counts"] == {}
    assert format_linear_planning_surface_summary(surface) == ""
