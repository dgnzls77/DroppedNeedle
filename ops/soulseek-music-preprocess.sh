#!/usr/bin/env bash
set -euo pipefail

WATCH_HOST="${WATCH_HOST:-/mnt/music/watch/nicotine}"
WATCH_CONTAINER="${WATCH_CONTAINER:-/watch/nicotine}"
PIPELINE_HOST="$WATCH_HOST/_pipeline"
REQUESTS_HOST="$PIPELINE_HOST/requests"
RECEIPTS_HOST="$PIPELINE_HOST/receipts"
BEETS_HOST="${BEETS_HOST:-/opt/tidarr/shared/beets}"
LOG_FILE="$BEETS_HOST/soulseek-pipeline.log"
LOCK_FILE="/run/soulseek-music-preprocess.lock"

install -d -m 775 "$REQUESTS_HOST" "$RECEIPTS_HOST"
install -d -m 775 "$BEETS_HOST/soulseek-tasks" "$BEETS_HOST/edition-cleanup-manifests"
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG_FILE"
}

write_receipt() {
  local task_id="$1"
  local status="$2"
  local error="${3:-}"
  python3 - "$RECEIPTS_HOST" "$task_id" "$status" "$error" <<'PY'
import json
import os
import pathlib
import sys

root, task_id, status, error = sys.argv[1:]
destination = pathlib.Path(root) / f"{task_id}.json"
temporary = destination.with_suffix(f".tmp-{os.getpid()}")
payload = {"version": 1, "task_id": task_id, "status": status}
if error:
    payload["error"] = error
with temporary.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, destination)
PY
}

request_target() {
  python3 - "$1" "$WATCH_HOST" "$WATCH_CONTAINER" <<'PY'
import json
import os
import pathlib
import sys

request_path, host_root_raw, container_root_raw = sys.argv[1:]
host_root = pathlib.Path(host_root_raw).resolve()
container_root = pathlib.PurePosixPath(container_root_raw)
payload = json.loads(pathlib.Path(request_path).read_text(encoding="utf-8"))
paths = payload.get("paths")
if payload.get("version") != 1 or not isinstance(paths, list) or not paths:
    raise SystemExit("invalid request")

resolved = []
for relative_raw in paths:
    relative = pathlib.PurePosixPath(str(relative_raw))
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit("unsafe path")
    host_path = (host_root / pathlib.Path(*relative.parts)).resolve()
    host_path.relative_to(host_root)
    if not host_path.is_file():
        raise SystemExit("source file missing")
    resolved.append(host_path)

if len(resolved) == 1:
    target = resolved[0]
else:
    common = pathlib.Path(os.path.commonpath([str(path.parent) for path in resolved]))
    if common == host_root:
        raise SystemExit("request spans unrelated download folders")
    target = common

relative_target = target.relative_to(host_root)
print(str(container_root.joinpath(*relative_target.parts)))
PY
}

if ! docker inspect -f '{{.State.Running}}' tidarr >/dev/null 2>&1; then
  log "tidarr container is not available"
  exit 1
fi

shopt -s nullglob
for request in "$REQUESTS_HOST"/*.json; do
  task_id="$(basename "$request" .json)"
  if [[ ! "$task_id" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]; then
    log "ignored request with unsafe task id"
    continue
  fi
  if [ -e "$RECEIPTS_HOST/$task_id.json" ]; then
    continue
  fi

  log "Soulseek pipeline start: $task_id"
  if ! target="$(request_target "$request")"; then
    write_receipt "$task_id" error "invalid or missing download files"
    log "Soulseek pipeline rejected request: $task_id"
    continue
  fi

  temp_db="/shared/beets/soulseek-tasks/$task_id.blb"
  import_log="/shared/beets/soulseek-tasks/$task_id-import.log"
  if ! timeout 30m docker exec tidarr beet \
      -c /shared/beets-config.yml \
      -l "$temp_db" \
      import -q --quiet-fallback=asis --log "$import_log" "$target" \
      >>"$LOG_FILE" 2>&1; then
    write_receipt "$task_id" error "Beets processing failed"
    log "Soulseek Beets failed: $task_id"
    continue
  fi

  manifest="/shared/beets/edition-cleanup-manifests/soulseek-$task_id.jsonl"
  if ! timeout 10m docker exec tidarr python3 \
      /shared/scripts/normalize-edition-tags.py \
      --root "$target" --apply --manifest "$manifest" \
      >>"$LOG_FILE" 2>&1; then
    write_receipt "$task_id" error "edition tag normalization failed"
    log "Soulseek edition normalization failed: $task_id"
    continue
  fi

  if ! timeout 30m docker exec tidarr sh -c '
      target="$1"
      if [ -f "$target" ]; then
        target="$(dirname "$target")"
      fi
      exec rsgain easy --skip-existing --multithread=2 "$target"
    ' sh "$target" >>"$LOG_FILE" 2>&1; then
    write_receipt "$task_id" error "ReplayGain processing failed"
    log "Soulseek ReplayGain failed: $task_id"
    continue
  fi

  write_receipt "$task_id" ok
  log "Soulseek pipeline complete: $task_id"
done
