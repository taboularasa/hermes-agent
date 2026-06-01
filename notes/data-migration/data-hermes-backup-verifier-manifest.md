# HAD-1160 /data/hermes backup verifier manifest

Issue: HAD-1160, backup verifier manifest for `/data/hermes` backup classes.

This artifact turns the HAD-1155 backup boundary into a recurring readback
manifest. It defines the evidence a backup operator must capture before
declaring `/data/hermes` backup coverage acceptable or before opening the
holdback cleanup window.

## Scope and guardrails

This document is planning-only and verification-only. It does not execute
backups, restores, migration commands, mounts, copies, deletes, ownership
changes, Docker stops, or systemd stops/starts. Commands below are read-only
pseudocommands unless explicitly marked as local inspection of a restored
readback directory.

The verifier target is the recurring backup of these known `/data/hermes`
subvolumes:

- `/data/hermes/profile-default`
- `/data/hermes/stacks`
- `/data/hermes/ctx-data`
- `/data/hermes/codex-home`
- `/data/hermes/.migration-evidence`, if created

Docker named volumes and service-specific source stores outside `/data/hermes`
remain outside this manifest unless a later issue adds them to the data-disk
backup boundary.

## Verifier manifest

Each row is a required readback class. A recurring readback report must either
include passing evidence for the class or record the class as failed with the
failure action below.

| Class | Paths/patterns | Readback proof | Expected cadence | Owner | Failure action | Migration/holdback relevance |
|---|---|---|---|---|---|---|
| `critical-durable` | `/data/hermes/profile-default`, `/data/hermes/stacks`, `/data/hermes/ctx-data`, `/data/hermes/codex-home`; Hermes `config.yaml`; `state.db`; session, gateway, cron, ctx, and Codex metadata; repo `.git` directories under stacks. | Backup readback listing names every required subvolume; checksum or backup-catalog identity is recorded for representative files; SQLite integrity check passes for `profile-default/state.db` from the readback directory; repo metadata under stacks is listable without exposing secrets. | Nightly readback summary when backup jobs run, plus an explicit pre-holdback-cleanup run after migration. | Hermes operator, with manager acceptance for holdback cleanup. | Mark verification failed, keep holdbacks, repair backup coverage, rerun readback, and do not start holdback cleanup. | This is the minimum state needed to resume Hermes, Codex, ctx, gateway, cron, and active project work after restoring `/data/hermes`. |
| `important-durable` | Provenance notes, backlog, implementation delegate records, self-improvement records, source materials, ontology and decision-room repositories, generated ops material, and project work under `/data/hermes/stacks` and `/data/hermes/profile-default/notes`. | Readback report samples representative files from each high-value family by path and size or checksum; repo worktrees can be enumerated; omissions are listed with manager signoff instead of silently passing. | Weekly, and before migration acceptance if any high-value family changed since the last readback. | Hermes operator for evidence, manager for accepted omissions. | Keep backup status degraded, create or update a follow-up issue for the missing family, and block holdback cleanup if the family was part of the migration source of truth. | Protects historical context, source materials, and provenance that may not be reproducible from public source or package rebuilds. |
| `operational-history` | `/data/hermes/profile-default/logs`, cron output, reports, `/data/hermes/.migration-evidence`, backup verification reports, restore rehearsal reports, and bounded rotated logs. | Readback lists the latest operational-history files and the retention window represented in backup storage; evidence includes at least one current report and one retained historical report when available. | Weekly, and after every migration rehearsal, backup policy change, or holdback gate review. | Hermes operator. | Record the audit gap, preserve local evidence until readback succeeds, and keep holdbacks if migration evidence is missing. | Gives the manager enough audit trail to prove why holdback deletion is safe and to debug restore regressions later. |
| `retention` | Backup catalog entries for `/data/hermes` snapshots, bounded logs, backup verification reports, restore rehearsal reports, and any configured retention tiers. | Readback report records newest successful snapshot, oldest retained snapshot, retention tier names, and whether operational-history retention is bounded as intended. | Weekly policy check, plus every time retention settings or backup destinations change. | Backup owner with Hermes operator review. | Treat backup policy as unverified, pause holdback cleanup, and update the retention rule or issue tracker before accepting the backup. | Prevents holdback deletion based on a single transient backup or an unbounded history policy that does not match HAD-1155. |
| `exclusion` | Excluded caches, virtualenvs, dependency directories, package caches, temp spill paths, stale WAL/SHM after writer quiescence, and migration holdback directories such as `/home/david/.hermes.pre-data-migration-holdback.${MIGRATION_ID}`. | Backup listing proves excluded patterns are absent from recurring backup artifacts while durable paths remain present; any promoted cache subtree is named with owner signoff. | Weekly, and before accepting a new path-contract inventory or backup rule. | Hermes operator. | Fix exclusion rules, rerun readback, and keep holdbacks if exclusions removed durable data or if holdback directories leaked into recurring backup scope. | Confirms the backup is neither bloated with rebuildable data nor missing durable data because of overly broad exclusions. |
| `holdback-gate` | HAD-1155 holdback directories, this HAD-1160 manifest, the latest readback report, SQLite/session checks, repo readback evidence, ctx and Codex metadata checks, and Docker named-volume boundary notes. | Gate evidence explicitly says pass or fail for every class above; it confirms this manifest and the readback report are themselves preserved in `/data/hermes/.migration-evidence` or the accepted evidence location; it records manager acceptance before cleanup. | Once after migration readback succeeds and again after any failed or retried holdback review. | Manager accepts; Hermes operator gathers evidence. | Holdbacks stay in place, cleanup remains blocked, and the failure is recorded with the class that blocked acceptance. | This is the final recurring artifact that decides whether temporary migration rollback copies can be removed. |

## Readback report template

Store each completed report under the accepted migration evidence location, for
example:

`/data/hermes/.migration-evidence/backup-verification/readback-YYYYMMDD.md`

The report must use these headings:

1. `Snapshot identity`
2. `critical-durable`
3. `important-durable`
4. `operational-history`
5. `retention`
6. `exclusion`
7. `holdback-gate`
8. `Failures and follow-ups`
9. `Manager acceptance`

Each class section must include:

- backup source or snapshot identity
- readback root or catalog location inspected
- paths or patterns checked
- proof captured without printing secrets
- pass or fail status
- owner and date
- failure action if not passing

## Read-only command set

The concrete backup client is intentionally not named here. Replace
`backup-client` with the real read-only catalog or readback command for the
deployed backup system. These are pseudocommands, not commands to run against
live data during this delegated planning task.

```text
# Show backup snapshots that include /data/hermes.
backup-client snapshots --read-only --path /data/hermes

# List files in the latest backup without restoring into live paths.
backup-client ls --read-only SNAPSHOT_ID -- /data/hermes/profile-default
backup-client ls --read-only SNAPSHOT_ID -- /data/hermes/stacks
backup-client ls --read-only SNAPSHOT_ID -- /data/hermes/ctx-data
backup-client ls --read-only SNAPSHOT_ID -- /data/hermes/codex-home

# Dry-run a readback into an isolated scratch location chosen by the operator.
backup-client readback --dry-run SNAPSHOT_ID --source /data/hermes --target READBACK_ROOT
```

After a separate, authorized restore/readback has populated `READBACK_ROOT`, the
operator may inspect only the readback copy:

```text
sqlite3 READBACK_ROOT/profile-default/state.db "PRAGMA integrity_check;"
git -C READBACK_ROOT/stacks/hermes-agent status --short
git -C READBACK_ROOT/stacks/hermes-agent fsck --no-progress
find READBACK_ROOT -maxdepth 3 -type d -name ".git" -print
find READBACK_ROOT -path "*/node_modules" -prune -o -path "*/.venv" -prune -o -print
```

Do not print secret file contents. It is acceptable to prove secret-bearing files
exist by path, size, mode, checksum from the backup catalog, or encrypted backup
metadata.

## Static repo verification

The repo-backed static verifier checks that this manifest still contains the
required classes, fields, guardrails, and report headings:

```text
python scripts/data_migration_backup_verifier.py
```

This static verifier only reads this repository file. It does not inspect
`/data/hermes`, backup storage, Docker, systemd, mounted filesystems, or live
host services.
