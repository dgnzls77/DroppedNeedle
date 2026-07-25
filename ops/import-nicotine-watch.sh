#!/usr/bin/env bash
set -euo pipefail

WATCH_HOST="/mnt/music/watch/nicotine"
WATCH_CONTAINER="/watch/nicotine"
LOG_DIR="/opt/tidarr/shared/beets"
LOG_FILE="$LOG_DIR/nicotine-import.log"
LRC_QUEUE_FILE="$LOG_DIR/nicotine-lrc-queue.txt"
LOCK_FILE="/run/nicotine-beets-import.lock"
MIN_AGE_MINUTES="${MIN_AGE_MINUTES:-30}"
mkdir -p "$LOG_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG_FILE"
}

is_pipeline_reserved() {
  local base="$1"
  python3 - "$WATCH_HOST/_pipeline/requests" "$base" <<'PY'
import json
import pathlib
import sys

requests, wanted = pathlib.Path(sys.argv[1]), sys.argv[2]
for request in requests.glob("*.json"):
    try:
        payload = json.loads(request.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    for raw_path in payload.get("paths") or []:
        parts = pathlib.PurePosixPath(str(raw_path)).parts
        if parts and parts[0] == wanted:
            raise SystemExit(0)
raise SystemExit(1)
PY
}

run_replaygain_for_source() {
  local container_path="$1"
  local base="$2"
  timeout 20m docker exec tidarr sh -lc '
    set -eu
    target="$1"
    if [ -f "$target" ]; then
      target="$(dirname "$target")"
    fi
    printf "[rsgain] %s\n" "$target"
    rsgain easy --skip-existing --multithread=2 "$target"
  ' sh "$container_path" >>"$LOG_FILE" 2>&1 ||
    log "ReplayGain source pass reported a non-fatal issue before $base"
}

if ! docker inspect -f '{{.State.Running}}' tidarr >/dev/null 2>&1; then
  log "tidarr container is not available"
  exit 1
fi
if [ ! -d "$WATCH_HOST" ]; then
  log "watch path missing: $WATCH_HOST"
  exit 1
fi

mapfile -d '' candidates < <(
  find "$WATCH_HOST" -mindepth 1 -maxdepth 1 \
    \( -type d -o -type f \) ! -name '.*' ! -name '_*' \
    -mmin +"$MIN_AGE_MINUTES" -print0 | sort -z
)
if [ "${#candidates[@]}" -eq 0 ]; then
  log "no stable candidates older than ${MIN_AGE_MINUTES}m"
  exit 0
fi

for host_path in "${candidates[@]}"; do
  base="$(basename "$host_path")"
  if is_pipeline_reserved "$base"; then
    log "skip DroppedNeedle-owned candidate: $base"
    continue
  fi
  container_path="$WATCH_CONTAINER/$base"
  if [ -d "$host_path" ]; then
    audio_count=$(
      find "$host_path" -type f \
        \( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.m4a' \
        -o -iname '*.ogg' -o -iname '*.opus' -o -iname '*.wav' \) |
        wc -l
    )
  else
    case "${base,,}" in
      *.flac|*.mp3|*.m4a|*.ogg|*.opus|*.wav) audio_count=1 ;;
      *) audio_count=0 ;;
    esac
  fi
  if [ "$audio_count" -eq 0 ]; then
    if [ -d "$host_path" ] &&
      [ -z "$(find "$host_path" -mindepth 1 -print -quit)" ]; then
      rmdir "$host_path" && log "removed empty candidate: $base"
    else
      log "skip non-audio candidate: $base"
    fi
    continue
  fi

  task_key="$(printf '%s' "$base-$(date +%s%N)" | sha256sum | cut -c1-20)"
  temp_db="/shared/beets/soulseek-tasks/manual-$task_key.blb"
  temp_log="/shared/beets/soulseek-tasks/manual-$task_key-import.log"
  cleanup_manifest="/shared/beets/edition-cleanup-manifests/manual-$task_key.jsonl"
  log "import start: $base ($audio_count audio files)"

  # First pass: use the same Beets configuration as Tidarr, but leave the
  # files in the watch folder so ReplayGain runs before the final move.
  if ! docker exec tidarr beet \
      -c /shared/beets-config.yml -l "$temp_db" \
      import -q --quiet-fallback=asis --log "$temp_log" "$container_path" \
      >>"$LOG_FILE" 2>&1; then
    log "Beets metadata pass failed: $base"
    continue
  fi
  if ! docker exec tidarr python3 /shared/scripts/normalize-edition-tags.py \
      --root "$container_path" --apply --manifest "$cleanup_manifest" \
      >>"$LOG_FILE" 2>&1; then
    log "edition normalization failed: $base"
    continue
  fi
  run_replaygain_for_source "$container_path" "$base"

  # Second pass is a metadata-preserving move into Tidarr's final naming
  # hierarchy and registration in the long-lived Beets database.
  marker="$LOG_DIR/.nicotine-import-marker"
  touch "$marker"
  if docker exec tidarr beet \
      -c /shared/beets-config.yml -l /shared/beets/beets-library.blb -d /music \
      import -A -m -P --log /shared/beets/nicotine-import-skipped.log \
      "$container_path" >>"$LOG_FILE" 2>&1; then
    log "beets import ok: $base"
    queued_count=$(
      find /mnt/music/library -type f \
        \( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.m4a' \
        -o -iname '*.ogg' -o -iname '*.opus' -o -iname '*.wav' \) \
        -newer "$marker" -print |
        sed 's#^/mnt/music/library#/music#' |
        tee -a "$LRC_QUEUE_FILE" |
        wc -l
    )
    if [ "$queued_count" -gt 0 ]; then
      sort -u "$LRC_QUEUE_FILE" -o "$LRC_QUEUE_FILE"
      log "queued for lrc fetch: $base ($queued_count audio files)"
    fi
    rm -f "/opt/tidarr/shared/beets/soulseek-tasks/manual-$task_key.blb"
  else
    log "beets final import failed: $base"
  fi
done
