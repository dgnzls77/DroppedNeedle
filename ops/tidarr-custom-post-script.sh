#!/bin/sh
set -eu

LIDARR_URL="${LIDARR_URL:-http://lidarr:8686}"
LIDARR_API_KEY_FILE="${LIDARR_API_KEY_FILE:-/shared/.lidarr-api-key}"
LIDARR_ROOT_PATH="${LIDARR_ROOT_PATH:-/music}"
EDITION_MANIFEST_DIR="${EDITION_MANIFEST_DIR:-/shared/beets/edition-cleanup-manifests}"

# Tidarr invokes this only after the final move. Normalize each moved artist
# folder before asking Lidarr/Plex to refresh, so cosmetic remaster suffixes
# never become the long-lived library title. This is deliberately non-fatal:
# metadata cleanup must not turn a completed audio import into a failed job.
mkdir -p "$EDITION_MANIFEST_DIR"
run_id="$(date +%Y%m%d-%H%M%S)-$$"
index=0
old_ifs="$IFS"
IFS=','
for raw_folder in ${FOLDERS_MOVED:-}; do
    IFS="$old_ifs"
    folder="$(printf '%s' "$raw_folder" | sed 's#^/*##; s#/*$##')"
    [ -n "$folder" ] || continue
    case "$folder" in
        "${LIDARR_ROOT_PATH#/}/"*) target="/$folder" ;;
        *) target="${LIDARR_ROOT_PATH%/}/$folder" ;;
    esac
    artist_tail="${target#${LIDARR_ROOT_PATH%/}/}"
    artist="${artist_tail%%/*}"
    target="${LIDARR_ROOT_PATH%/}/$artist"
    index=$((index + 1))
    manifest="$EDITION_MANIFEST_DIR/tidarr-$run_id-$index.jsonl"
    if [ -d "$target" ]; then
        if ! python3 /shared/scripts/normalize-edition-tags.py \
            --root "$target" --apply --manifest "$manifest"; then
            echo "Edition-title normalization reported a non-fatal issue for $target"
        fi
    fi
    IFS=','
done
IFS="$old_ifs"

if [ ! -r "$LIDARR_API_KEY_FILE" ]; then
  echo "Lidarr API key file is not readable; skipping Lidarr rescan"
  exit 0
fi

python3 - <<'PY'
import json
import os
import sys
import urllib.request

lidarr_url = os.environ.get("LIDARR_URL", "http://lidarr:8686").rstrip("/")
key_file = os.environ.get("LIDARR_API_KEY_FILE", "/shared/.lidarr-api-key")
root_path = os.environ.get("LIDARR_ROOT_PATH", "/music").rstrip("/")
folders_moved = os.environ.get("FOLDERS_MOVED", "")

try:
    with open(key_file, "r", encoding="utf-8") as handle:
        api_key = handle.read().strip()
except OSError as exc:
    print(f"Lidarr API key file could not be read: {exc}")
    sys.exit(0)

if not api_key:
    print("Lidarr API key file is empty; skipping Lidarr rescan")
    sys.exit(0)

artist_paths = []
for raw_folder in folders_moved.split(","):
    folder = raw_folder.strip().strip("/")
    if not folder:
        continue
    if folder.startswith(root_path.strip("/") + "/"):
        folder = folder[len(root_path.strip("/")) + 1 :]
    artist = folder.split("/", 1)[0].strip()
    if artist:
        artist_paths.append(f"{root_path}/{artist}")

artist_paths = sorted(set(artist_paths))
if not artist_paths:
    print("No moved artist folders found; skipping Lidarr rescan")
    sys.exit(0)

headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

def request_json(path, payload=None):
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        method = "POST"
    req = urllib.request.Request(
        f"{lidarr_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)

try:
    artists = request_json("/api/v1/artist")
except Exception as exc:
    print(f"Lidarr artist lookup failed: {exc}")
    sys.exit(0)

known_paths = {artist.get("path") for artist in artists if artist.get("path")}
for artist_path in artist_paths:
    if artist_path not in known_paths:
        print(f"No Lidarr artist found at {artist_path}; skipping")
        continue
    payload = {
        "name": "RescanFolders",
        "folders": [artist_path],
        "filter": "matched",
        "addNewArtists": False,
    }
    try:
        result = request_json("/api/v1/command", payload)
    except Exception as exc:
        print(f"Lidarr rescan queue failed for {artist_path}: {exc}")
        continue
    command_id = result.get("id", "unknown")
    status = result.get("status", "unknown")
    print(f"Queued Lidarr rescan for {artist_path} (command {command_id}, {status})")
PY
