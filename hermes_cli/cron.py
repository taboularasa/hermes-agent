# HADTO-PATCH: cron topology
"""
Cron subcommand for hermes CLI.

Handles standalone cron management commands like list, create, edit,
pause/resume/run/remove, status, and tick.
"""

import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from hermes_cli.colors import Colors, color


def _normalize_skills(single_skill=None, skills: Optional[Iterable[str]] = None) -> Optional[List[str]]:
    if skills is None:
        if single_skill is None:
            return None
        raw_items = [single_skill]
    else:
        raw_items = list(skills)

    normalized: List[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _cron_api(**kwargs):
    from tools.cronjob_tools import cronjob as cronjob_tool

    return json.loads(cronjob_tool(**kwargs))


def cron_list(show_all: bool = False):
    """List all scheduled jobs."""
    from cron.jobs import list_jobs

    jobs = list_jobs(include_disabled=show_all)

    if not jobs:
        print(color("No scheduled jobs.", Colors.DIM))
        print(color("Create one with 'hermes cron create ...' or the /cron command in chat.", Colors.DIM))
        return

    print()
    print(color("┌─────────────────────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│                         Scheduled Jobs                                  │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────────────────────┘", Colors.CYAN))
    print()

    for job in jobs:
        job_id = job.get("id", "?")[:8]
        name = job.get("name", "(unnamed)")
        schedule = job.get("schedule_display", job.get("schedule", {}).get("value", "?"))
        state = job.get("state", "scheduled" if job.get("enabled", True) else "paused")
        next_run = job.get("next_run_at", "?")

        repeat_info = job.get("repeat", {})
        repeat_times = repeat_info.get("times")
        repeat_completed = repeat_info.get("completed", 0)
        repeat_str = f"{repeat_completed}/{repeat_times}" if repeat_times else "∞"

        deliver = job.get("deliver", ["local"])
        if isinstance(deliver, str):
            deliver = [deliver]
        deliver_str = ", ".join(deliver)

        skills = job.get("skills") or ([job["skill"]] if job.get("skill") else [])
        if state == "paused":
            status = color("[paused]", Colors.YELLOW)
        elif state == "completed":
            status = color("[completed]", Colors.BLUE)
        elif job.get("enabled", True):
            status = color("[active]", Colors.GREEN)
        else:
            status = color("[disabled]", Colors.RED)

        print(f"  {color(job_id, Colors.YELLOW)} {status}")
        print(f"    Name:      {name}")
        print(f"    Schedule:  {schedule}")
        print(f"    Repeat:    {repeat_str}")
        print(f"    Next run:  {next_run}")
        print(f"    Deliver:   {deliver_str}")
        if skills:
            print(f"    Skills:    {', '.join(skills)}")
        if job.get("role") or job.get("scope"):
            print(
                f"    Topology:  role={job.get('role') or '-'}"
                f", scope={job.get('scope') or '-'}"
            )
        if state == "paused" and job.get("paused_reason"):
            print(f"    Reason:    {job['paused_reason']}")
        print()

    from hermes_cli.gateway import find_gateway_pids
    if not find_gateway_pids():
        print(color("  ⚠  Gateway is not running — jobs won't fire automatically.", Colors.YELLOW))
        print(color("     Start it with: hermes gateway install", Colors.DIM))
        print(color("                    sudo hermes gateway install --system  # Linux servers", Colors.DIM))
        print()


def cron_tick():
    """Run due jobs once and exit."""
    from cron.scheduler import tick
    tick(verbose=True)


def cron_status():
    """Show cron execution status."""
    from cron.jobs import list_jobs
    from hermes_cli.gateway import find_gateway_pids

    print()

    pids = find_gateway_pids()
    if pids:
        print(color("✓ Gateway is running — cron jobs will fire automatically", Colors.GREEN))
        print(f"  PID: {', '.join(map(str, pids))}")
    else:
        print(color("✗ Gateway is not running — cron jobs will NOT fire", Colors.RED))
        print()
        print("  To enable automatic execution:")
        print("    hermes gateway install    # Install as a user service")
        print("    sudo hermes gateway install --system  # Linux servers: boot-time system service")
        print("    hermes gateway            # Or run in foreground")

    print()

    jobs = list_jobs(include_disabled=False)
    if jobs:
        next_runs = [j.get("next_run_at") for j in jobs if j.get("next_run_at")]
        print(f"  {len(jobs)} active job(s)")
        if next_runs:
            print(f"  Next run: {min(next_runs)}")
    else:
        print("  No active jobs")

    print()


def cron_topology(show_all: bool = False):
    """Show grouped cron topology and any overlap issues."""
    from cron.jobs import inspect_job_topology

    snapshot = inspect_job_topology(include_disabled=show_all)

    print()
    print(color("Cron Topology", Colors.CYAN))
    print(color("-------------", Colors.CYAN))
    print(
        f"  Active jobs: {snapshot['summary']['active_jobs']}  "
        f"Inactive jobs: {snapshot['summary']['inactive_jobs']}  "
        f"Issues: {snapshot['summary']['issue_count']}"
    )
    print()

    grouped = snapshot.get("grouped_active_jobs", {})
    if grouped:
        for role in sorted(grouped):
            print(color(f"  {role}", Colors.YELLOW))
            for scope in sorted(grouped[role]):
                jobs = grouped[role][scope]
                print(f"    {scope}")
                for job in jobs:
                    print(
                        f"      - {job['name']} ({job['id'][:8]})"
                        f"  {job.get('schedule_display', job.get('next_run_at', '?'))}"
                    )
            print()

    unclassified = snapshot.get("unclassified_active_jobs", [])
    if unclassified:
        print(color("  Unclassified", Colors.YELLOW))
        for job in unclassified:
            print(f"    - {job['name']} ({job['id'][:8]})")
        print()

    if show_all:
        inactive = snapshot.get("inactive_jobs", [])
        if inactive:
            print(color("  Inactive / Paused", Colors.YELLOW))
            for job in inactive:
                reason = job.get("paused_reason")
                suffix = f" — {reason}" if reason else ""
                print(f"    - {job['name']} ({job['id'][:8]}){suffix}")
            print()

    if snapshot.get("issues"):
        print(color("  Issues", Colors.RED))
        for issue in snapshot["issues"]:
            tint = Colors.RED if issue.get("severity") == "error" else Colors.YELLOW
            print(f"    {color(issue['severity'].upper(), tint)} {issue['message']}")
        print()
    else:
        print(color("  No topology conflicts detected.", Colors.GREEN))
        print()


def cron_doctor(show_all: bool = True) -> int:
    """Lint cron topology for duplicate names and overlapping implementation jobs."""
    from cron.jobs import inspect_job_topology

    snapshot = inspect_job_topology(include_disabled=show_all)
    print()
    if snapshot.get("issues"):
        print(color("Cron Doctor", Colors.CYAN))
        print(color("-----------", Colors.CYAN))
        for issue in snapshot["issues"]:
            tint = Colors.RED if issue.get("severity") == "error" else Colors.YELLOW
            print(f"  {color(issue['severity'].upper(), tint)} {issue['message']}")
        print()
    else:
        print(color("Cron Doctor: no conflicts detected.", Colors.GREEN))
        print()

    return 1 if any(issue.get("severity") == "error" for issue in snapshot.get("issues", [])) else 0


def cron_create(args):
    result = _cron_api(
        action="create",
        schedule=args.schedule,
        prompt=args.prompt,
        name=getattr(args, "name", None),
        deliver=getattr(args, "deliver", None),
        repeat=getattr(args, "repeat", None),
        skill=getattr(args, "skill", None),
        skills=_normalize_skills(getattr(args, "skill", None), getattr(args, "skills", None)),
        role=getattr(args, "role", None),
        scope=getattr(args, "scope", None),
    )
    if not result.get("success"):
        print(color(f"Failed to create job: {result.get('error', 'unknown error')}", Colors.RED))
        return 1
    print(color(f"Created job: {result['job_id']}", Colors.GREEN))
    print(f"  Name: {result['name']}")
    print(f"  Schedule: {result['schedule']}")
    if result.get("skills"):
        print(f"  Skills: {', '.join(result['skills'])}")
    if result.get("job", {}).get("role") or result.get("job", {}).get("scope"):
        print(
            f"  Topology: role={result['job'].get('role') or '-'}, "
            f"scope={result['job'].get('scope') or '-'}"
        )
    print(f"  Next run: {result['next_run_at']}")
    return 0


def cron_edit(args):
    from cron.jobs import get_job

    job = get_job(args.job_id)
    if not job:
        print(color(f"Job not found: {args.job_id}", Colors.RED))
        return 1

    existing_skills = list(job.get("skills") or ([] if not job.get("skill") else [job.get("skill")]))
    replacement_skills = _normalize_skills(getattr(args, "skill", None), getattr(args, "skills", None))
    add_skills = _normalize_skills(None, getattr(args, "add_skills", None)) or []
    remove_skills = set(_normalize_skills(None, getattr(args, "remove_skills", None)) or [])

    final_skills = None
    if getattr(args, "clear_skills", False):
        final_skills = []
    elif replacement_skills is not None:
        final_skills = replacement_skills
    elif add_skills or remove_skills:
        final_skills = [skill for skill in existing_skills if skill not in remove_skills]
        for skill in add_skills:
            if skill not in final_skills:
                final_skills.append(skill)

    result = _cron_api(
        action="update",
        job_id=args.job_id,
        schedule=getattr(args, "schedule", None),
        prompt=getattr(args, "prompt", None),
        name=getattr(args, "name", None),
        deliver=getattr(args, "deliver", None),
        repeat=getattr(args, "repeat", None),
        skills=final_skills,
        role=getattr(args, "role", None),
        scope=getattr(args, "scope", None),
    )
    if not result.get("success"):
        print(color(f"Failed to update job: {result.get('error', 'unknown error')}", Colors.RED))
        return 1

    updated = result["job"]
    print(color(f"Updated job: {updated['job_id']}", Colors.GREEN))
    print(f"  Name: {updated['name']}")
    print(f"  Schedule: {updated['schedule']}")
    if updated.get("skills"):
        print(f"  Skills: {', '.join(updated['skills'])}")
    else:
        print("  Skills: none")
    if updated.get("role") or updated.get("scope"):
        print(f"  Topology: role={updated.get('role') or '-'}, scope={updated.get('scope') or '-'}")
    return 0


def _job_action(action: str, job_id: str, success_verb: str, **kwargs) -> int:
    result = _cron_api(action=action, job_id=job_id, **kwargs)
    if not result.get("success"):
        print(color(f"Failed to {action} job: {result.get('error', 'unknown error')}", Colors.RED))
        return 1
    job = result.get("job") or result.get("removed_job") or {}
    print(color(f"{success_verb} job: {job.get('name', job_id)} ({job_id})", Colors.GREEN))
    if action in {"resume", "run"} and result.get("job", {}).get("next_run_at"):
        print(f"  Next run: {result['job']['next_run_at']}")
    if action == "run":
        print("  It will run on the next scheduler tick.")
    if action == "pause" and result.get("job", {}).get("paused_reason"):
        print(f"  Reason: {result['job']['paused_reason']}")
    return 0


def cron_command(args):
    """Handle cron subcommands."""
    subcmd = getattr(args, 'cron_command', None)

    if subcmd is None or subcmd == "list":
        show_all = getattr(args, 'all', False)
        cron_list(show_all)
        return 0

    if subcmd == "status":
        cron_status()
        return 0

    if subcmd == "topology":
        cron_topology(show_all=getattr(args, "all", False))
        return 0

    if subcmd == "doctor":
        return cron_doctor(show_all=True)

    if subcmd == "tick":
        cron_tick()
        return 0

    if subcmd in {"create", "add"}:
        return cron_create(args)

    if subcmd == "edit":
        return cron_edit(args)

    if subcmd == "pause":
        return _job_action("pause", args.job_id, "Paused", reason=getattr(args, "reason", None))

    if subcmd == "resume":
        return _job_action("resume", args.job_id, "Resumed")

    if subcmd == "run":
        return _job_action("run", args.job_id, "Triggered")

    if subcmd in {"remove", "rm", "delete"}:
        return _job_action("remove", args.job_id, "Removed")

    print(f"Unknown cron command: {subcmd}")
    print("Usage: hermes cron [list|create|edit|pause|resume|run|remove|status|tick]")
    sys.exit(1)
