# HADTO-PATCH: misc
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


STACKS_ROOT = Path("/home/david/stacks")
_UI_CONTAINER_EXCLUDES = {"watchtower", "ontology-validator", "ontology-api"}
_PROCESS_NAME_EXCLUDES = {"agent-browser-l"}

_KNOWN_APPS = {
    "hadto-pipeline": {
        "title": "Hadto Pipeline",
        "description": "Venture scoring, discovery, validation, and studio economics dashboard.",
    },
    "hadto-ontology-workbench": {
        "title": "Hadto Ontology Workbench",
        "description": "Ontology search and journey workbench for browsing Hadto terms, provenance, and business flows.",
    },
    "ontology-explainer": {
        "title": "Ontology Explainer",
        "description": "Interactive ontology diagram and explainer UI for the multi-layer model.",
    },
    "ontology-triplestore": {
        "title": "Oxigraph Triplestore",
        "description": "SPARQL and graph browser UI for the SMB ontology dataset.",
        "repo_name": "smb-ontology-platform",
    },
}


@dataclass(frozen=True)
class HostApp:
    title: str
    description: str
    link: str
    repo_url: str


@dataclass(frozen=True)
class _Candidate:
    slug: str
    repo_name: str | None
    address: str
    port: int
    title: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class _TailnetInfo:
    ip: str
    dns_name: str
    served_urls_by_port: dict[int, str]


def _run_text(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def _normalize_repo_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url.split(":", 1)[1]
    elif url.startswith("ssh://git@github.com/"):
        url = "https://github.com/" + url.split("github.com/", 1)[1]
    if url.endswith(".git"):
        url = url[:-4]
    return url


def _read_git_origin(repo_path: Path) -> str:
    config_path = repo_path / ".git" / "config"
    if not config_path.exists():
        return ""
    in_origin = False
    for line in config_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_origin = stripped == '[remote "origin"]'
            continue
        if in_origin:
            match = re.match(r"url\s*=\s*(.+)", stripped)
            if match:
                return _normalize_repo_url(match.group(1).strip())
    return ""


def _first_readme_paragraph(repo_path: Path) -> str:
    for name in ("README.md", "README.mdx", "README.txt"):
        path = repo_path / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
        for block in blocks:
            if block.startswith("#"):
                continue
            line = " ".join(part.strip() for part in block.splitlines()).strip()
            if line:
                return re.sub(r"\s+", " ", line)
    return ""


def _read_package_name(repo_path: Path) -> str:
    path = repo_path / "package.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(data.get("name") or "").strip()


def _read_next_metadata(repo_path: Path) -> tuple[str, str]:
    for relative in ("app/layout.tsx", "src/app/layout.tsx"):
        path = repo_path / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        title_match = re.search(r"title:\s*['\"]([^'\"]+)['\"]", text)
        desc_match = re.search(r"description:\s*['\"]([^'\"]+)['\"]", text)
        return (
            title_match.group(1).strip() if title_match else "",
            desc_match.group(1).strip() if desc_match else "",
        )
    return "", ""


def _prettify_name(name: str) -> str:
    name = name.replace("_", "-")
    parts = [part for part in name.split("-") if part]
    if not parts:
        return name
    return " ".join(part.upper() if len(part) <= 3 else part.capitalize() for part in parts)


def _inventory_repos(root: Path) -> dict[str, dict[str, str]]:
    repos: dict[str, dict[str, str]] = {}
    if not root.exists():
        return repos
    for child in root.iterdir():
        if not child.is_dir() or not (child / ".git").exists():
            continue
        metadata_title, metadata_desc = _read_next_metadata(child)
        package_name = _read_package_name(child)
        readme_desc = _first_readme_paragraph(child)
        known = _KNOWN_APPS.get(child.name, {})
        repos[child.name] = {
            "repo_url": _read_git_origin(child),
            "title": known.get("title") or metadata_title or _prettify_name(package_name or child.name),
            "description": known.get("description") or metadata_desc or readme_desc,
        }
    return repos


def _build_link(address: str, port: int, tailscale_ip: str) -> str:
    host = address.strip("[]")
    if host in {"127.0.0.1", "localhost"}:
        host = "127.0.0.1"
    elif host in {"0.0.0.0", "*", "::", "[::]"}:
        host = tailscale_ip or "127.0.0.1"
    scheme = "https" if port == 443 else "http"
    return f"{scheme}://{host}:{port}" if port not in {80, 443} else f"{scheme}://{host}"


def _tailnet_info() -> _TailnetInfo:
    ip = _run_text(["tailscale", "ip", "-4"]).strip().splitlines()
    ip = ip[0].strip() if ip else ""

    dns_name = ""
    status_raw = _run_text(["tailscale", "status", "--json"])
    if status_raw:
        try:
            status = json.loads(status_raw)
            dns_name = str(status.get("Self", {}).get("DNSName") or "").strip().rstrip(".")
        except Exception:
            dns_name = ""

    served_urls_by_port: dict[int, str] = {}
    serve_raw = _run_text(["tailscale", "serve", "status", "--json"])
    if serve_raw:
        try:
            serve_status = json.loads(serve_raw)
            for hostport, config in (serve_status.get("Web") or {}).items():
                handlers = (config or {}).get("Handlers") or {}
                host = hostport.rsplit(":", 1)[0]
                for path, handler in handlers.items():
                    proxy = str((handler or {}).get("Proxy") or "").strip()
                    match = re.match(r"https?://(?:127\.0\.0\.1|localhost):(?P<port>\d+)(?P<suffix>/.*)?$", proxy)
                    if not match:
                        continue
                    exposed_port = int(match.group("port"))
                    suffix = "" if path == "/" else path
                    served_urls_by_port[exposed_port] = f"https://{host}{suffix}"
        except Exception:
            served_urls_by_port = {}

    return _TailnetInfo(ip=ip, dns_name=dns_name, served_urls_by_port=served_urls_by_port)


def _preferred_link(candidate: _Candidate, tailnet: _TailnetInfo) -> str:
    if candidate.port in tailnet.served_urls_by_port:
        return tailnet.served_urls_by_port[candidate.port]

    host = candidate.address.strip("[]")
    tailnet_host = tailnet.dns_name or tailnet.ip
    if tailnet_host and host not in {"127.0.0.1", "localhost"}:
        scheme = "https" if candidate.port == 443 else "http"
        return (
            f"{scheme}://{tailnet_host}"
            if candidate.port in {80, 443}
            else f"{scheme}://{tailnet_host}:{candidate.port}"
        )

    return _build_link(candidate.address, candidate.port, tailnet.ip)


def _parse_published_port(ports_text: str) -> tuple[str, int] | None:
    for chunk in (part.strip() for part in ports_text.split(",")):
        match = re.search(r"([0-9a-fA-F:.\[\]*]+):(\d+)->(\d+)/(tcp|udp)", chunk)
        if match and match.group(4) == "tcp":
            return match.group(1), int(match.group(2))
    return None


def _docker_candidates() -> list[_Candidate]:
    raw = _run_text(["docker", "ps", "--format", "{{json .}}"])
    candidates: list[_Candidate] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(row.get("Names") or "").strip()
        if not name or name in _UI_CONTAINER_EXCLUDES:
            continue
        published = _parse_published_port(str(row.get("Ports") or ""))
        if not published:
            continue
        address, port = published
        known = _KNOWN_APPS.get(name, {})
        candidates.append(
            _Candidate(
                slug=name,
                repo_name=known.get("repo_name") or name,
                address=address,
                port=port,
                title=known.get("title"),
                description=known.get("description"),
            )
        )
    return candidates


def _repo_name_from_pid(pid: str, root: Path) -> str | None:
    try:
        cwd = Path(os.readlink(f"/proc/{pid}/cwd"))
    except OSError:
        return None
    try:
        rel = cwd.resolve().relative_to(root.resolve())
    except Exception:
        return None
    parts = rel.parts
    return parts[0] if parts else None


def _process_candidates(root: Path) -> list[_Candidate]:
    raw = _run_text(["ss", "-H", "-ltnp"])
    candidates: list[_Candidate] = []
    for line in raw.splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) < 5:
            continue
        local = parts[3]
        process = parts[5] if len(parts) > 5 else ""
        if any(name in process for name in _PROCESS_NAME_EXCLUDES):
            continue
        pid_match = re.search(r"pid=(\d+)", process)
        if not pid_match:
            continue
        repo_name = _repo_name_from_pid(pid_match.group(1), root)
        if not repo_name:
            continue
        port_match = re.search(r"(.+):(\d+)$", local)
        if not port_match:
            continue
        address, port_text = port_match.groups()
        port = int(port_text)
        known = _KNOWN_APPS.get(repo_name, {})
        candidates.append(
            _Candidate(
                slug=f"{repo_name}:{port}",
                repo_name=repo_name,
                address=address,
                port=port,
                title=known.get("title"),
                description=known.get("description"),
            )
        )
    return candidates


def _dedupe_candidates(candidates: Iterable[_Candidate]) -> list[_Candidate]:
    seen: set[tuple[str | None, int]] = set()
    deduped: list[_Candidate] = []
    for candidate in candidates:
        key = (candidate.repo_name, candidate.port)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def discover_host_apps(root: Path | str = STACKS_ROOT) -> list[HostApp]:
    root = Path(root)
    tailnet = _tailnet_info()
    repo_inventory = _inventory_repos(root)
    candidates = _dedupe_candidates([*_docker_candidates(), *_process_candidates(root)])
    apps: list[HostApp] = []
    seen_links: set[str] = set()
    for candidate in candidates:
        repo_meta = repo_inventory.get(candidate.repo_name or "", {})
        title = candidate.title or repo_meta.get("title") or _prettify_name(candidate.repo_name or candidate.slug)
        description = candidate.description or repo_meta.get("description") or "Running web UI on the Lenovo host."
        repo_url = repo_meta.get("repo_url", "")
        link = _preferred_link(candidate, tailnet)
        if link in seen_links:
            continue
        seen_links.add(link)
        apps.append(HostApp(title=title, description=description, link=link, repo_url=repo_url))
    return sorted(apps, key=lambda item: item.title.lower())


def format_host_apps_markdown(apps: Iterable[HostApp]) -> str:
    items = list(apps)
    if not items:
        return "No running dashboards, apps, or web UIs detected on the Lenovo host."
    lines = ["🖥️ **Running dashboards, apps, and UIs on Lenovo**"]
    for app in items:
        repo_part = f" Repo: {app.repo_url}" if app.repo_url else ""
        lines.append(f"- [{app.title}]({app.link}) — {app.description}{repo_part}")
    return "\n".join(lines)
