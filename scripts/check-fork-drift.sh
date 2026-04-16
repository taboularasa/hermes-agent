#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
FILE_BASELINE=""
if [[ -f "$ROOT/PATCH_BASELINE.json" ]]; then
  FILE_BASELINE="$(python3 -c "import json; print(json.load(open('$ROOT/PATCH_BASELINE.json'))['patch_count'])")"
fi
BASELINE="${HADTO_PATCH_BASELINE:-${FILE_BASELINE:-46}}"
WINDOW="${HADTO_PATCH_WINDOW:-5}"
COMPARE_REF=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --against)
      COMPARE_REF="${2:-}"
      shift 2
      ;;
    --window)
      WINDOW="${2:-}"
      shift 2
      ;;
    --baseline)
      BASELINE="${2:-}"
      shift 2
      ;;
    *)
      echo "usage: $0 [--against <git-ref>] [--window <lines>] [--baseline <count>]" >&2
      exit 2
      ;;
  esac
done

cd "$ROOT"

tmp_annotations="$(mktemp)"
trap 'rm -f "$tmp_annotations"' EXIT

rg -n "HADTO-PATCH" --glob '*.py' > "$tmp_annotations" || true

count="$(wc -l < "$tmp_annotations" | tr -d '[:space:]')"
echo "HADTO-PATCH baseline: $count locations"
cat "$tmp_annotations"

status=0
if [[ "$count" != "$BASELINE" ]]; then
  echo
  echo "WARNING: expected $BASELINE HADTO-PATCH locations, found $count" >&2
  status=1
fi

if [[ -n "$COMPARE_REF" ]]; then
  echo
  echo "Checking for upstream proximity against $COMPARE_REF (window: +/-$WINDOW lines)"
  python3 - "$tmp_annotations" "$COMPARE_REF" "$WINDOW" <<'PY'
import pathlib
import re
import subprocess
import sys

annotation_path = pathlib.Path(sys.argv[1])
compare_ref = sys.argv[2]
window = int(sys.argv[3])
repo = pathlib.Path.cwd()

annotations: dict[str, list[int]] = {}
for line in annotation_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    path, line_no, _rest = line.split(":", 2)
    annotations.setdefault(path, []).append(int(line_no))

cmd = ["git", "diff", "--unified=0", f"{compare_ref}...HEAD", "--", "*.py"]
diff = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=False)
if diff.returncode not in (0, 1):
    print(f"WARNING: failed to diff against {compare_ref}: {diff.stderr.strip()}", file=sys.stderr)
    sys.exit(1)

current_file = None
warnings: list[str] = []
hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
for raw in diff.stdout.splitlines():
    if raw.startswith("+++ b/"):
        current_file = raw[6:]
        continue
    match = hunk_re.match(raw)
    if not match or current_file not in annotations:
        continue
    start = int(match.group(1))
    length = int(match.group(2) or "1")
    end = start + max(length, 1) - 1
    for marker in annotations[current_file]:
        if start <= marker + window and end >= marker - window:
            warnings.append(
                f"{current_file}:{marker} is within {window} lines of diff hunk +{start}-{end}"
            )

if warnings:
    print("WARNING: upstream-touch proximity detected:", file=sys.stderr)
    for entry in warnings:
        print(f"  - {entry}", file=sys.stderr)
    sys.exit(1)
PY
  [[ $? -eq 0 ]] || status=1
fi

exit "$status"
