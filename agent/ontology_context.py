"""Compact reasoning context derived from Hadto ontology platform artifacts."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


DEFAULT_ONTOLOGY_REPO_ROOT = Path("/home/david/stacks/smb-ontology-platform")

# Keep these lightweight and operator-readable. The ontology tool should surface
# domain leverage, not dump the entire graph into a prompt.
CORE_BOUNDED_CONTEXTS = {
    "Scheduling": {"schedule", "scheduling", "appointment", "booking", "dispatch", "calendar"},
    "CRM": {"crm", "client", "customer", "lead", "prospect", "account", "intake"},
    "Invoicing": {"invoice", "invoicing", "billing", "payment", "claim", "charge", "royalty"},
    "Communications": {"communication", "communications", "email", "sms", "call", "outreach", "reminder"},
    "Compliance": {"compliance", "permit", "inspection", "hipaa", "conflict", "filing", "audit"},
    "HR": {"hr", "staff", "employee", "shift", "technician", "provider", "hiring"},
    "Inventory": {"inventory", "menu", "materials", "equipment", "stock"},
    "Reporting": {"report", "reporting", "metrics", "dashboard", "analytics", "kpi"},
}

VERTICAL_ALIASES = {
    "dental": {"dental", "dentist", "dentists", "tooth", "teeth", "patient", "recall", "insurance"},
    "home_services": {"home", "hvac", "plumbing", "electrical", "dispatch", "permit", "inspection", "property"},
    "professional_services": {"professional", "legal", "law", "lawyer", "firm", "consulting", "matter", "time_entry"},
    "franchise_operations": {"franchise", "restaurant", "retail", "unit", "royalty", "menu", "pos", "shift"},
}

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "their",
    "about",
    "what",
    "when",
    "where",
    "which",
    "while",
    "through",
    "would",
    "could",
    "should",
    "have",
    "into",
    "them",
    "they",
    "were",
    "been",
    "being",
    "more",
    "less",
    "than",
    "then",
    "over",
    "under",
    "just",
    "only",
    "need",
    "needs",
    "work",
    "works",
    "working",
    "help",
    "helps",
    "system",
    "systems",
    "business",
    "company",
}

PRIORITY_WEIGHTS = {
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_yaml(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9_]+", text.lower())
        if len(token) > 2 and token not in STOPWORDS
    ]


def _normalize_vertical_name(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def _extract_item_text(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    texts: list[str] = []
    for item in items:
        if isinstance(item, str):
            if item.strip():
                texts.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        for key in ("title", "name", "summary", "description", "problem", "question", "rationale"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())
        refs = item.get("signals")
        if isinstance(refs, list):
            texts.extend(_extract_item_text(refs))
    return texts


def _top_groups(cqs: Any, *, limit: int = 3) -> list[str]:
    counts: Counter[str] = Counter()
    if isinstance(cqs, list):
        for cq in cqs:
            if not isinstance(cq, dict):
                continue
            group = str(cq.get("group") or "").strip()
            if group:
                counts[group] += 1
    return [group for group, _count in counts.most_common(limit)]


def _priority_rank(value: Any) -> int:
    return PRIORITY_WEIGHTS.get(str(value or "").strip().lower(), 0)


def _file_timestamp(path: Path) -> Optional[datetime]:
    if not path.exists():
        return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _generated_at_from_report(report_text: str) -> Optional[datetime]:
    match = re.search(r"Generated at:\s*`([^`]+)`", report_text)
    if not match:
        return None
    return _parse_timestamp(match.group(1))


def _latest_timestamp(paths: Iterable[Path]) -> Optional[datetime]:
    timestamps = [_file_timestamp(path) for path in paths]
    values = [value for value in timestamps if value is not None]
    return max(values) if values else None


def _artifact_status(latest: Optional[datetime], now: datetime, freshness_hours: int) -> dict[str, Any]:
    if latest is None:
        return {"status": "missing", "latest_timestamp": None, "age_hours": None}
    age_hours = round((now - latest).total_seconds() / 3600, 2)
    return {
        "status": "fresh" if age_hours < freshness_hours else "stale",
        "latest_timestamp": latest.isoformat(),
        "age_hours": age_hours,
    }


def _manifest_timestamp(path: Path) -> Optional[datetime]:
    payload = _load_yaml(path)
    if not isinstance(payload, dict):
        return _file_timestamp(path)
    timestamp = _parse_timestamp(payload.get("prepared_at"))
    if timestamp:
        return timestamp
    sources = payload.get("sources")
    if isinstance(sources, list):
        candidates = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            ts = _parse_timestamp(source.get("captured_at"))
            if ts:
                candidates.append(ts)
        if candidates:
            return max(candidates)
    return _file_timestamp(path)


def _proposal_timestamp(path: Path) -> Optional[datetime]:
    payload = _load_yaml(path)
    if isinstance(payload, dict):
        timestamp = _parse_timestamp(payload.get("generated_at"))
        if timestamp:
            return timestamp
    return _file_timestamp(path)


def _log_timestamp(path: Path) -> Optional[datetime]:
    payload = _load_json(path)
    if isinstance(payload, dict):
        timestamp = _parse_timestamp(payload.get("timestamp"))
        if timestamp:
            return timestamp
    return _file_timestamp(path)


def _latest_manifest_timestamp(paths: Iterable[Path]) -> Optional[datetime]:
    candidates = [_manifest_timestamp(path) for path in paths]
    values = [value for value in candidates if value is not None]
    return max(values) if values else None


def _latest_yaml_timestamp(paths: Iterable[Path]) -> Optional[datetime]:
    candidates = [_proposal_timestamp(path) for path in paths]
    values = [value for value in candidates if value is not None]
    return max(values) if values else None


def _latest_json_timestamp(paths: Iterable[Path]) -> Optional[datetime]:
    candidates = [_log_timestamp(path) for path in paths]
    values = [value for value in candidates if value is not None]
    return max(values) if values else None


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for entry in path.rglob("*") if entry.is_file())


def _summarize_agenda(path: Path, *, limit: int = 3) -> Optional[dict[str, Any]]:
    payload = _load_yaml(path)
    if not isinstance(payload, dict):
        return None
    vertical = _normalize_vertical_name(str(payload.get("vertical") or path.stem))
    topics = payload.get("topics", [])
    open_questions = 0
    active_topics: list[dict[str, Any]] = []
    stale_topics: list[str] = []
    if isinstance(topics, list):
        for topic in topics:
            if not isinstance(topic, dict):
                continue
            open_count = int(topic.get("open_questions") or 0)
            open_questions += open_count
            if int(topic.get("stale_cycles") or 0) > 0:
                stale_topics.append(str(topic.get("topic") or "").strip())
            active_topics.append(
                {
                    "topic": str(topic.get("topic") or "").strip(),
                    "priority": str(topic.get("priority") or "").strip(),
                    "open_questions": open_count,
                    "answered_questions": int(topic.get("answered_questions") or 0),
                    "times_seen": int(topic.get("times_seen") or 0),
                    "last_seen_cycle": topic.get("last_seen_cycle"),
                    "stale_cycles": int(topic.get("stale_cycles") or 0),
                    "sources": list(topic.get("sources", [])) if isinstance(topic.get("sources"), list) else [],
                }
            )
    active_topics.sort(
        key=lambda item: (-_priority_rank(item.get("priority")), -item.get("open_questions", 0), item.get("topic", "")),
    )
    return {
        "vertical": vertical,
        "last_updated": payload.get("last_updated"),
        "open_questions": open_questions,
        "active_topics": active_topics[:limit],
        "stale_topics": [topic for topic in stale_topics if topic][:limit],
        "path": str(path),
    }


def _latest_retrospective(retrospective_dir: Path) -> Optional[dict[str, Any]]:
    if not retrospective_dir.exists():
        return None
    candidates = []
    for path in retrospective_dir.glob("cycle-*.yaml"):
        payload = _load_yaml(path)
        if not isinstance(payload, dict):
            continue
        cycle = payload.get("cycle")
        generated_at = _parse_timestamp(payload.get("generated_at")) or _file_timestamp(path)
        candidates.append((cycle if isinstance(cycle, int) else -1, generated_at, path, payload))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1] or datetime.min.replace(tzinfo=timezone.utc)))
    _, _, path, payload = candidates[-1]
    priority_candidates = payload.get("priority_candidates", [])
    trimmed_candidates = []
    if isinstance(priority_candidates, list):
        for candidate in priority_candidates[:3]:
            if not isinstance(candidate, dict):
                continue
            trimmed_candidates.append(
                {
                    "topic": candidate.get("topic"),
                    "priority": candidate.get("priority"),
                    "open_questions": candidate.get("open_questions"),
                    "question_count": candidate.get("question_count"),
                    "sources": candidate.get("sources", []),
                }
            )
    return {
        "vertical": _normalize_vertical_name(str(payload.get("vertical") or retrospective_dir.name)),
        "cycle": payload.get("cycle"),
        "generated_at": payload.get("generated_at"),
        "novelty_score": payload.get("novelty_score"),
        "answer_path_quality": payload.get("answer_path_quality"),
        "prompt_effectiveness": payload.get("prompt_effectiveness"),
        "business_relevance": payload.get("business_relevance"),
        "research_signal_changed": payload.get("research_signal_changed"),
        "priority_candidates": trimmed_candidates,
        "path": str(path),
    }


def _extract_manifest_sources(payload: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    summaries = []
    for source in sources[:limit]:
        if not isinstance(source, dict):
            continue
        blob_store = source.get("blob_store") if isinstance(source.get("blob_store"), dict) else {}
        summaries.append(
            {
                "source_id": source.get("source_id"),
                "source_iri": source.get("source_iri"),
                "title": source.get("title"),
                "kind": source.get("kind"),
                "captured_at": source.get("captured_at"),
                "sha256": source.get("sha256"),
                "size_bytes": source.get("size_bytes"),
                "original_url": source.get("original_url"),
                "final_url": source.get("final_url"),
                "stored_path": source.get("stored_path"),
                "blob_store": {
                    "bucket": blob_store.get("bucket"),
                    "endpoint": blob_store.get("endpoint"),
                    "object_key": blob_store.get("object_key"),
                    "uri": blob_store.get("uri"),
                }
                if blob_store
                else None,
            }
        )
    return summaries


def _summarize_manifest(path: Path, *, source_limit: int = 3) -> Optional[dict[str, Any]]:
    payload = _load_yaml(path)
    if not isinstance(payload, dict):
        return None
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    return {
        "manifest_id": payload.get("manifest_id"),
        "manifest_iri": payload.get("manifest_iri"),
        "prepared_at": payload.get("prepared_at"),
        "source_count": len(sources),
        "sources": _extract_manifest_sources(payload, limit=source_limit),
        "path": str(path),
    }


def _vertical_profile(
    path: Path,
    *,
    metrics: dict[str, Any],
    delta: dict[str, Any],
) -> dict[str, Any]:
    payload = _load_yaml(path) or {}
    vertical = _normalize_vertical_name(str(payload.get("vertical") or path.stem))
    competency_questions = payload.get("competency_questions", [])
    system_problems = _extract_item_text(payload.get("system_problems"))
    use_cases = _extract_item_text(payload.get("ontology_use_cases"))
    glossary = _extract_item_text(payload.get("term_glossary"))
    top_groups = _top_groups(competency_questions)
    research_discovery_count = sum(
        1
        for cq in competency_questions
        if isinstance(cq, dict) and str(cq.get("source") or "").strip() == "research_discovery"
    )
    text_corpus = " ".join(
        [
            vertical.replace("_", " "),
            str(payload.get("purpose") or ""),
            str(payload.get("scope") or ""),
            " ".join(system_problems),
            " ".join(use_cases),
            " ".join(glossary),
            " ".join(top_groups),
        ]
    )
    metric_entry = metrics.get(vertical, {}) if isinstance(metrics, dict) else {}
    delta_entry = delta.get(vertical, {}) if isinstance(delta, dict) else {}
    return {
        "vertical": vertical,
        "last_evolved": payload.get("last_evolved"),
        "purpose": str(payload.get("purpose") or "").strip(),
        "scope": str(payload.get("scope") or "").strip(),
        "system_problems": system_problems,
        "use_cases": use_cases,
        "top_groups": top_groups,
        "research_discovery_count": research_discovery_count,
        "terms": sorted(set(_tokenize(text_corpus)) | VERTICAL_ALIASES.get(vertical, set())),
        "metrics": metric_entry,
        "delta": delta_entry,
    }


def load_ontology_snapshot(repo_root: Path | str = DEFAULT_ONTOLOGY_REPO_ROOT) -> dict[str, Any]:
    root = Path(repo_root).expanduser()
    metrics_path = root / "evolution" / "metrics.json"
    delta_path = root / "evolution" / "delta_report.json"
    daily_report_path = root / "evolution" / "daily_report.md"
    logs_dir = root / "evolution" / "logs"
    manifests_dir = root / "research" / "manifests"
    prompt_proposals_dir = root / "research" / "prompt_proposals"
    source_store_dir = root / "research" / "source_store"
    orsd_dir = root / "orsd"

    metrics = _load_json(metrics_path) or {}
    delta_report = _load_json(delta_path) or {}
    daily_report = _load_text(daily_report_path)
    logs = sorted(logs_dir.glob("*.json"))
    manifests = sorted(manifests_dir.rglob("*.yaml"))
    prompt_proposals = sorted(prompt_proposals_dir.rglob("*.yaml"))

    metric_verticals = metrics.get("verticals", {}) if isinstance(metrics, dict) else {}
    delta_verticals = (
        delta_report.get("current", {}).get("verticals", {})
        if isinstance(delta_report, dict)
        else {}
    )

    vertical_profiles = [
        _vertical_profile(path, metrics=metric_verticals, delta=delta_verticals)
        for path in sorted(orsd_dir.glob("*.yaml"))
    ]

    productization_candidate = None
    differentiated_candidate = None
    if vertical_profiles:
        with_reuse = [
            profile
            for profile in vertical_profiles
            if isinstance(profile.get("metrics"), dict)
            and profile["metrics"].get("foundation_reuse_ratio") is not None
        ]
        if with_reuse:
            productization_candidate = max(
                with_reuse,
                key=lambda profile: float(profile["metrics"].get("foundation_reuse_ratio") or 0.0),
            )
            differentiated_candidate = min(
                with_reuse,
                key=lambda profile: float(profile["metrics"].get("foundation_reuse_ratio") or 0.0),
            )

    latest_log = _latest_json_timestamp(logs)
    latest_manifest = _latest_manifest_timestamp(manifests)
    latest_prompt_proposal = _latest_yaml_timestamp(prompt_proposals)

    latest_manifests = []
    for path in sorted(manifests, key=lambda item: _manifest_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:5]:
        payload = _load_yaml(path) or {}
        sources = payload.get("sources", []) if isinstance(payload, dict) else []
        latest_manifests.append(
            {
                "manifest_id": payload.get("manifest_id") if isinstance(payload, dict) else None,
                "path": str(path),
                "prepared_at": (
                    _manifest_timestamp(path).isoformat()
                    if _manifest_timestamp(path) is not None
                    else None
                ),
                "source_count": len(sources) if isinstance(sources, list) else 0,
                "source_titles": [
                    str(source.get("title") or source.get("source_id") or "").strip()
                    for source in sources[:3]
                    if isinstance(source, dict)
                ],
            }
        )

    return {
        "repo_root": str(root),
        "paths": {
            "metrics": str(metrics_path),
            "delta_report": str(delta_path),
            "daily_report": str(daily_report_path),
            "logs_dir": str(logs_dir),
            "manifests_dir": str(manifests_dir),
            "prompt_proposals_dir": str(prompt_proposals_dir),
            "source_store_dir": str(source_store_dir),
            "orsd_dir": str(orsd_dir),
        },
        "generated_at": metrics.get("generated_at") if isinstance(metrics, dict) else None,
        "platform": metrics.get("platform", {}) if isinstance(metrics, dict) else {},
        "verticals": vertical_profiles,
        "business_recommendations": (
            delta_report.get("business_recommendations", [])
            if isinstance(delta_report, dict)
            else []
        ),
        "learnings": delta_report.get("learnings", []) if isinstance(delta_report, dict) else [],
        "research_assets": {
            "manifest_count": len(manifests),
            "source_store_files": _count_files(source_store_dir),
            "prompt_proposal_count": len(prompt_proposals),
            "evolution_log_count": len(logs),
            "latest_manifest_at": latest_manifest.isoformat() if latest_manifest else None,
            "latest_prompt_proposal_at": latest_prompt_proposal.isoformat() if latest_prompt_proposal else None,
            "latest_evolution_log_at": latest_log.isoformat() if latest_log else None,
            "latest_manifests": latest_manifests,
        },
        "artifacts": {
            "metrics_generated_at": metrics.get("generated_at") if isinstance(metrics, dict) else None,
            "delta_report_generated_at": delta_report.get("generated_at") if isinstance(delta_report, dict) else None,
            "daily_report_generated_at": _generated_at_from_report(daily_report).isoformat()
            if daily_report
            else None,
            "latest_evolution_log_at": latest_log.isoformat() if latest_log else None,
            "latest_manifest_at": latest_manifest.isoformat() if latest_manifest else None,
            "latest_prompt_proposal_at": latest_prompt_proposal.isoformat() if latest_prompt_proposal else None,
        },
        "candidates": {
            "productization": {
                "vertical": productization_candidate["vertical"],
                "foundation_reuse_ratio": productization_candidate["metrics"].get("foundation_reuse_ratio"),
                "cq_total": productization_candidate["metrics"].get("cq_total"),
            }
            if productization_candidate
            else None,
            "differentiation": {
                "vertical": differentiated_candidate["vertical"],
                "foundation_reuse_ratio": differentiated_candidate["metrics"].get("foundation_reuse_ratio"),
                "cq_total": differentiated_candidate["metrics"].get("cq_total"),
            }
            if differentiated_candidate
            else None,
        },
    }


def _compute_use_case_readiness(orsd: dict[str, Any]) -> dict[str, Any]:
    cqs = [cq for cq in orsd.get("competency_questions", []) if isinstance(cq, dict)]
    cq_index = {cq.get("id"): cq for cq in cqs if cq.get("id")}
    use_cases = [use_case for use_case in orsd.get("ontology_use_cases", []) if isinstance(use_case, dict)]
    use_case_results: list[dict[str, Any]] = []
    for use_case in use_cases:
        supporting_ids = [cq_id for cq_id in use_case.get("supporting_cqs", []) if cq_id in cq_index]
        answered_ids = [cq_id for cq_id in supporting_ids if cq_index[cq_id].get("status") == "answered"]
        total = len(supporting_ids)
        answered = len(answered_ids)
        blocked_by = [cq_id for cq_id in supporting_ids if cq_id not in answered_ids]
        coverage = round(answered / total, 3) if total else 0.0
        status = "ready" if total and answered == total else ("partial" if answered else "blocked")
        use_case_results.append(
            {
                "id": use_case.get("id"),
                "title": use_case.get("title"),
                "status": status,
                "coverage": coverage,
                "answered_cqs": answered,
                "total_cqs": total,
                "blocked_by": blocked_by,
                "problem_refs": use_case.get("problem_refs", []),
            }
        )

    problem_index = {
        problem.get("id"): problem
        for problem in orsd.get("system_problems", [])
        if isinstance(problem, dict) and problem.get("id")
    }
    problem_to_use_cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for use_case in use_case_results:
        for problem_ref in use_case.get("problem_refs", []):
            problem_to_use_cases[problem_ref].append(use_case)

    problem_results: list[dict[str, Any]] = []
    for problem_id, problem in problem_index.items():
        linked_use_cases = problem_to_use_cases.get(problem_id, [])
        total = len(linked_use_cases)
        ready = sum(1 for use_case in linked_use_cases if use_case.get("status") == "ready")
        partial = sum(1 for use_case in linked_use_cases if use_case.get("status") == "partial")
        problem_results.append(
            {
                "id": problem_id,
                "category": problem.get("category", "unknown"),
                "statement": problem.get("statement", ""),
                "use_case_total": total,
                "use_case_ready": ready,
                "use_case_partial": partial,
                "coverage": round(ready / total, 3) if total else 0.0,
            }
        )

    ready_use_cases = sum(1 for use_case in use_case_results if use_case.get("status") == "ready")
    use_case_total = len(use_case_results)
    summary = {
        "ready_use_cases": ready_use_cases,
        "total_use_cases": use_case_total,
        "use_case_coverage": round(ready_use_cases / use_case_total, 4) if use_case_total else 0.0,
        "total_problems": len(problem_results),
        "problems_with_ready_use_cases": sum(
            1 for problem in problem_results if problem.get("use_case_ready", 0) > 0
        ),
    }

    return {
        "use_cases": use_case_results,
        "problems": problem_results,
        "summary": summary,
    }


def build_vertical_readiness_context(
    repo_root: Path | str = DEFAULT_ONTOLOGY_REPO_ROOT,
    *,
    vertical: Optional[str] = None,
    limit: int = 4,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser()
    snapshot = load_ontology_snapshot(root)
    orsd_dir = root / "orsd"
    vertical_filter = _normalize_vertical_name(vertical) if vertical else None

    readiness_profiles: list[dict[str, Any]] = []
    for path in sorted(orsd_dir.glob("*.yaml")):
        payload = _load_yaml(path)
        if not isinstance(payload, dict):
            continue
        vertical_name = _normalize_vertical_name(str(payload.get("vertical") or path.stem))
        if vertical_filter and vertical_name != vertical_filter:
            continue
        traceability = _compute_use_case_readiness(payload)
        metrics = next(
            (
                profile.get("metrics")
                for profile in snapshot.get("verticals", [])
                if _normalize_vertical_name(str(profile.get("vertical") or "")) == vertical_name
            ),
            {},
        )
        readiness_profiles.append(
            {
                "vertical": vertical_name,
                "last_evolved": payload.get("last_evolved"),
                "use_case_summary": traceability.get("summary", {}),
                "use_cases": traceability.get("use_cases", [])[:limit],
                "problems": traceability.get("problems", [])[:limit],
                "metrics": metrics if isinstance(metrics, dict) else {},
            }
        )

    readiness_profiles.sort(key=lambda item: item.get("vertical", ""))
    overall_ready = sum(
        int(profile.get("use_case_summary", {}).get("ready_use_cases") or 0)
        for profile in readiness_profiles
    )
    overall_total = sum(
        int(profile.get("use_case_summary", {}).get("total_use_cases") or 0)
        for profile in readiness_profiles
    )
    overall_coverage = round(overall_ready / overall_total, 4) if overall_total else 0.0

    return {
        "mode": "vertical_readiness",
        "verticals": readiness_profiles,
        "summary": {
            "ready_use_cases": overall_ready,
            "total_use_cases": overall_total,
            "use_case_coverage": overall_coverage,
        },
        "evidence": {
            "repo_root": snapshot.get("repo_root"),
            "orsd_dir": snapshot.get("paths", {}).get("orsd_dir"),
            "metrics_path": snapshot.get("paths", {}).get("metrics"),
        },
    }


def summarize_ontology_reliability(
    snapshot: dict[str, Any],
    *,
    now: Optional[datetime] = None,
    freshness_hours: int = 72,
) -> dict[str, Any]:
    current = now or datetime.now(tz=timezone.utc)
    artifacts = snapshot.get("artifacts", {}) if isinstance(snapshot, dict) else {}
    artifact_times = {
        "ontology_metrics": _parse_timestamp(artifacts.get("metrics_generated_at")),
        "ontology_delta_report": _parse_timestamp(artifacts.get("delta_report_generated_at")),
        "ontology_daily_report": _parse_timestamp(artifacts.get("daily_report_generated_at")),
        "ontology_evolution_logs": _parse_timestamp(artifacts.get("latest_evolution_log_at")),
        "ontology_manifests": _parse_timestamp(artifacts.get("latest_manifest_at")),
        "ontology_prompt_proposals": _parse_timestamp(artifacts.get("latest_prompt_proposal_at")),
    }
    statuses = {
        name: _artifact_status(timestamp, current, freshness_hours)
        for name, timestamp in artifact_times.items()
    }

    required_artifacts = ("ontology_metrics", "ontology_delta_report", "ontology_daily_report")
    reasons: list[str] = []
    for name in required_artifacts:
        status = statuses[name]["status"]
        if status == "missing":
            reasons.append(f"{name} missing")
        elif status == "stale":
            reasons.append(f"{name} stale ({statuses[name]['age_hours']}h)")

    required_values = [artifact_times[name] for name in required_artifacts if artifact_times[name] is not None]
    freshness_spread_hours = None
    if len(required_values) >= 2:
        freshness_spread_hours = round((max(required_values) - min(required_values)).total_seconds() / 3600, 2)
        if freshness_spread_hours >= freshness_hours:
            reasons.append("ontology artifact freshness mismatch across required reports")

    platform = snapshot.get("platform", {}) if isinstance(snapshot, dict) else {}
    total_cqs_added = int(platform.get("total_cqs_added") or 0)
    total_proposals_generated = int(platform.get("total_proposals_generated") or 0)
    conversion_bottleneck = {
        "active": total_cqs_added > 0 and total_proposals_generated == 0,
        "reason": (
            f"Ontology added {total_cqs_added} competency questions but generated 0 proposals."
            if total_cqs_added > 0 and total_proposals_generated == 0
            else None
        ),
    }

    alerts: list[str] = []
    if conversion_bottleneck["active"]:
        alerts.append(str(conversion_bottleneck["reason"]))

    latest_candidates = [timestamp for timestamp in artifact_times.values() if timestamp is not None]
    latest = max(latest_candidates) if latest_candidates else None
    if any(statuses[name]["status"] == "missing" for name in required_artifacts):
        status = "missing"
    elif reasons:
        status = "stale"
    else:
        status = "fresh"

    return {
        "status": status,
        "latest_timestamp": latest.isoformat() if latest is not None else None,
        "age_hours": round((current - latest).total_seconds() / 3600, 2) if latest is not None else None,
        "freshness_spread_hours": freshness_spread_hours,
        "artifacts": statuses,
        "reasons": reasons,
        "alerts": alerts,
        "conversion_bottleneck": conversion_bottleneck,
        "business_recommendations": snapshot.get("business_recommendations", []),
        "productization_candidate": snapshot.get("candidates", {}).get("productization"),
        "differentiation_candidate": snapshot.get("candidates", {}).get("differentiation"),
        "research_assets": snapshot.get("research_assets", {}),
    }


def _match_core_contexts(query: str, *, limit: int = 4) -> list[dict[str, Any]]:
    query_terms = set(_tokenize(query))
    matches = []
    for name, keywords in CORE_BOUNDED_CONTEXTS.items():
        overlap = sorted(query_terms & keywords)
        if not overlap:
            continue
        matches.append({"name": name, "matched_terms": overlap, "score": len(overlap)})
    matches.sort(key=lambda item: (-item["score"], item["name"]))
    return matches[:limit]


def rank_verticals(
    snapshot: dict[str, Any],
    *,
    query: str,
    vertical: Optional[str] = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    explicit_vertical = _normalize_vertical_name(vertical) if vertical else None
    query_terms = set(_tokenize(query))
    ranked: list[dict[str, Any]] = []
    for profile in snapshot.get("verticals", []):
        candidate_vertical = _normalize_vertical_name(str(profile.get("vertical") or ""))
        if explicit_vertical and candidate_vertical != explicit_vertical:
            continue
        terms = set(profile.get("terms", []))
        overlap = sorted(query_terms & terms)
        metrics = profile.get("metrics", {}) if isinstance(profile.get("metrics"), dict) else {}
        score = len(overlap) * 5
        if explicit_vertical and candidate_vertical == explicit_vertical:
            score += 100
        score += int(metrics.get("cq_total") or 0) // 25
        if metrics.get("foundation_reuse_ratio") is not None:
            score += int(float(metrics.get("foundation_reuse_ratio") or 0.0) * 3)
        if overlap or explicit_vertical:
            ranked.append(
                {
                    "vertical": candidate_vertical,
                    "score": score,
                    "matched_terms": overlap,
                    "top_groups": profile.get("top_groups", []),
                    "system_problems": profile.get("system_problems", [])[:3],
                    "use_cases": profile.get("use_cases", [])[:3],
                    "metrics": metrics,
                }
            )

    if not ranked and not explicit_vertical:
        for profile in snapshot.get("verticals", []):
            metrics = profile.get("metrics", {}) if isinstance(profile.get("metrics"), dict) else {}
            ranked.append(
                {
                    "vertical": _normalize_vertical_name(str(profile.get("vertical") or "")),
                    "score": int(metrics.get("cq_total") or 0) // 25,
                    "matched_terms": [],
                    "top_groups": profile.get("top_groups", []),
                    "system_problems": profile.get("system_problems", [])[:3],
                    "use_cases": profile.get("use_cases", [])[:3],
                    "metrics": metrics,
                }
            )
    ranked.sort(key=lambda item: (-item["score"], item["vertical"]))
    return ranked[:limit]


def _proof_points(snapshot: dict[str, Any], vertical_match: dict[str, Any]) -> list[str]:
    points = []
    platform = snapshot.get("platform", {})
    if platform:
        total_cqs = platform.get("total_cqs")
        total_answered = platform.get("total_answered")
        if total_cqs and total_answered is not None:
            points.append(f"Platform currently covers {total_answered}/{total_cqs} competency questions.")
    metrics = vertical_match.get("metrics", {})
    cq_total = metrics.get("cq_total")
    coverage = metrics.get("cq_coverage")
    if cq_total is not None and coverage is not None:
        points.append(
            f"{vertical_match['vertical']} has {cq_total} modeled questions at {float(coverage):.0%} coverage."
        )
    reuse_ratio = metrics.get("foundation_reuse_ratio")
    if reuse_ratio is not None:
        points.append(
            f"{vertical_match['vertical']} reuses the shared foundation at {float(reuse_ratio):.1%}."
        )
    top_groups = vertical_match.get("top_groups", [])
    if top_groups:
        points.append(f"Top modeled areas: {', '.join(top_groups[:3])}.")
    return points


def build_consulting_context(
    *,
    query: str,
    repo_root: Path | str = DEFAULT_ONTOLOGY_REPO_ROOT,
    vertical: Optional[str] = None,
    limit: int = 3,
) -> dict[str, Any]:
    snapshot = load_ontology_snapshot(repo_root)
    matches = rank_verticals(snapshot, query=query, vertical=vertical, limit=limit)
    contexts = _match_core_contexts(query)
    top_match = matches[0] if matches else None

    discovery_questions: list[str] = []
    recommended_next_steps: list[str] = []
    if top_match:
        for group in top_match.get("top_groups", [])[:2]:
            discovery_questions.append(
                f"Which workflows around {group.replace('_', ' ')} still depend on manual handoffs or spreadsheet state?"
            )
        for context in contexts[:2]:
            discovery_questions.append(
                f"What system is the current source of truth for {context['name']} data and where does it break down?"
            )
        recommended_next_steps.extend(
            [
                f"Map the client brief to the {top_match['vertical']} ontology and confirm the key entities/operators.",
                "Convert the discovery answers into bounded contexts, system-of-record boundaries, and verification questions.",
                "Use source-material capture when client evidence or website research needs durable provenance.",
            ]
        )

    return {
        "query": query,
        "mode": "consulting",
        "matched_verticals": matches,
        "core_contexts": contexts,
        "discovery_questions": discovery_questions,
        "proof_points": _proof_points(snapshot, top_match) if top_match else [],
        "recommended_next_steps": recommended_next_steps,
        "business_recommendations": snapshot.get("business_recommendations", [])[:3],
        "evidence": {
            "repo_root": snapshot.get("repo_root"),
            "metrics_path": snapshot.get("paths", {}).get("metrics"),
            "delta_report_path": snapshot.get("paths", {}).get("delta_report"),
            "latest_manifest_at": snapshot.get("research_assets", {}).get("latest_manifest_at"),
        },
    }


def build_sales_context(
    *,
    query: str,
    repo_root: Path | str = DEFAULT_ONTOLOGY_REPO_ROOT,
    vertical: Optional[str] = None,
    limit: int = 3,
) -> dict[str, Any]:
    snapshot = load_ontology_snapshot(repo_root)
    matches = rank_verticals(snapshot, query=query, vertical=vertical, limit=limit)
    contexts = _match_core_contexts(query)
    top_match = matches[0] if matches else None
    outreach_angles: list[str] = []
    discovery_prompts: list[str] = []
    if top_match:
        reuse_ratio = float(top_match.get("metrics", {}).get("foundation_reuse_ratio") or 0.0)
        if reuse_ratio >= 0.7:
            outreach_angles.append(
                f"Lead with repeatable packaging: {top_match['vertical']} is heavily aligned to the shared foundation, so Hermes can frame a faster path to delivery."
            )
        else:
            outreach_angles.append(
                f"Lead with differentiated depth: {top_match['vertical']} has lower shared-foundation reuse, which supports a higher-value niche positioning."
            )
        for group in top_match.get("top_groups", [])[:2]:
            discovery_prompts.append(
                f"How do you currently manage {group.replace('_', ' ')} and what slows revenue or client response time down?"
            )
        for context in contexts[:2]:
            discovery_prompts.append(
                f"Which {context['name']} workflow is currently most manual or error-prone?"
            )

    return {
        "query": query,
        "mode": "sales",
        "matched_verticals": matches,
        "core_contexts": contexts,
        "outreach_angles": outreach_angles,
        "discovery_prompts": discovery_prompts,
        "proof_points": _proof_points(snapshot, top_match) if top_match else [],
        "business_recommendations": snapshot.get("business_recommendations", [])[:3],
        "evidence": {
            "repo_root": snapshot.get("repo_root"),
            "delta_report_path": snapshot.get("paths", {}).get("delta_report"),
            "latest_manifest_at": snapshot.get("research_assets", {}).get("latest_manifest_at"),
        },
    }


def build_self_improvement_context(
    repo_root: Path | str = DEFAULT_ONTOLOGY_REPO_ROOT,
    *,
    now: Optional[datetime] = None,
    freshness_hours: int = 72,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser()
    snapshot = load_ontology_snapshot(root)
    reliability = summarize_ontology_reliability(snapshot, now=now, freshness_hours=freshness_hours)

    agenda_dir = root / "research" / "agenda"
    retrospective_dir = root / "research" / "retrospectives"
    agenda_summaries = [
        summary for path in sorted(agenda_dir.glob("*.yaml"))
        if (summary := _summarize_agenda(path)) is not None
    ]
    retrospective_summaries = []
    if retrospective_dir.exists():
        for vertical_dir in sorted(retrospective_dir.iterdir()):
            if not vertical_dir.is_dir():
                continue
            latest = _latest_retrospective(vertical_dir)
            if latest:
                retrospective_summaries.append(latest)

    gaps: list[dict[str, Any]] = []
    for agenda in agenda_summaries:
        open_questions = int(agenda.get("open_questions") or 0)
        if open_questions > 0:
            gaps.append(
                {
                    "vertical": agenda.get("vertical"),
                    "gap_type": "open_research_questions",
                    "detail": f"{open_questions} open agenda questions remain.",
                    "evidence": agenda.get("path"),
                }
            )

    for retro in retrospective_summaries:
        for metric, threshold in (
            ("answer_path_quality", 0.6),
            ("prompt_effectiveness", 0.6),
            ("business_relevance", 0.6),
        ):
            score = retro.get(metric)
            if isinstance(score, (int, float)) and score < threshold:
                gaps.append(
                    {
                        "vertical": retro.get("vertical"),
                        "gap_type": metric,
                        "detail": f"{metric}={score:.2f} below {threshold:.2f} target.",
                        "evidence": retro.get("path"),
                    }
                )

    for learning in snapshot.get("learnings", [])[:5]:
        if not isinstance(learning, dict):
            continue
        if learning.get("type") == "stale_vertical":
            gaps.append(
                {
                    "vertical": None,
                    "gap_type": "stale_verticals",
                    "detail": learning.get("detail"),
                    "evidence": snapshot.get("paths", {}).get("delta_report"),
                }
            )

    maintenance_candidates = []
    if reliability["status"] != "fresh":
        maintenance_candidates.append(
            {
                "lane": "Maintenance",
                "title": "Repair stale ontology intelligence artifacts",
                "why_now": ", ".join(reliability["reasons"]) or "Required ontology reports are missing or stale.",
            }
        )

    growth_candidates = []
    if reliability["conversion_bottleneck"]["active"]:
        growth_candidates.append(
            {
                "lane": "Growth",
                "title": "Turn ontology research discoveries into proposals and backlog",
                "why_now": reliability["conversion_bottleneck"]["reason"],
            }
        )

    productization = reliability.get("productization_candidate")
    if isinstance(productization, dict) and productization.get("vertical"):
        growth_candidates.append(
            {
                "lane": "Growth",
                "title": f"Package {productization['vertical']} as a repeatable offer",
                "why_now": (
                    f"{productization['vertical']} shows the strongest shared-foundation reuse "
                    f"({float(productization.get('foundation_reuse_ratio') or 0.0):.1%})."
                ),
            }
        )

    differentiation = reliability.get("differentiation_candidate")
    capability_candidates = []
    if isinstance(differentiation, dict) and differentiation.get("vertical"):
        capability_candidates.append(
            {
                "lane": "Capability",
                "title": f"Deepen differentiated modeling for {differentiation['vertical']}",
                "why_now": (
                    f"{differentiation['vertical']} has the lowest shared-foundation reuse "
                    f"({float(differentiation.get('foundation_reuse_ratio') or 0.0):.1%}), "
                    "which points to higher-value niche depth."
                ),
            }
        )

    return {
        "mode": "self_improvement",
        "reliability": reliability,
        "gaps": gaps,
        "agenda": agenda_summaries,
        "retrospectives": retrospective_summaries,
        "candidates": {
            "maintenance": maintenance_candidates,
            "growth": growth_candidates,
            "capability": capability_candidates,
        },
        "business_recommendations": snapshot.get("business_recommendations", [])[:3],
        "evidence": {
            "repo_root": snapshot.get("repo_root"),
            "metrics_path": snapshot.get("paths", {}).get("metrics"),
            "delta_report_path": snapshot.get("paths", {}).get("delta_report"),
            "daily_report_path": snapshot.get("paths", {}).get("daily_report"),
        },
    }


def build_source_material_context(
    repo_root: Path | str = DEFAULT_ONTOLOGY_REPO_ROOT,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser()
    snapshot = load_ontology_snapshot(root)
    research_assets = snapshot.get("research_assets", {})
    manifests_dir = root / "research" / "manifests"
    manifest_paths = sorted(manifests_dir.rglob("*.yaml"))
    manifest_samples = []
    for path in sorted(
        manifest_paths,
        key=lambda item: _manifest_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:limit]:
        summary = _summarize_manifest(path, source_limit=3)
        if summary:
            manifest_samples.append(summary)
    return {
        "mode": "source_materials",
        "research_assets": research_assets,
        "latest_manifests": list(research_assets.get("latest_manifests", []))[:limit],
        "manifest_samples": manifest_samples,
        "evidence": {
            "repo_root": snapshot.get("repo_root"),
            "manifests_dir": snapshot.get("paths", {}).get("manifests_dir"),
            "source_store_dir": snapshot.get("paths", {}).get("source_store_dir"),
        },
    }


def build_vertical_detail(
    *,
    repo_root: Path | str = DEFAULT_ONTOLOGY_REPO_ROOT,
    vertical: str,
) -> dict[str, Any]:
    snapshot = load_ontology_snapshot(repo_root)
    wanted = _normalize_vertical_name(vertical)
    for profile in snapshot.get("verticals", []):
        if _normalize_vertical_name(str(profile.get("vertical") or "")) == wanted:
            return {
                "mode": "vertical_detail",
                "vertical": profile,
                "proof_points": _proof_points(
                    snapshot,
                    {
                        "vertical": profile.get("vertical"),
                        "metrics": profile.get("metrics", {}),
                        "top_groups": profile.get("top_groups", []),
                    },
                ),
                "business_recommendations": snapshot.get("business_recommendations", [])[:3],
            }
    raise ValueError(f"Ontology vertical not found: {vertical}")
