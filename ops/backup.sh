#!/bin/bash
# Pull the droplet's Laminar data to the Mac as a dated, hardlinked snapshot.
#
# WHY THIS EXISTS: the collected data lives in exactly one place. The minute
# book series cannot be backfilled from REST — lose the droplet and the
# observation window is gone, not delayed. The pre-registration listed an
# off-box backup as a remaining task on 2026-06-14 and it was never done.
#
# WHY --link-dest AND NOT --delete: a backup must not propagate a loss. If the
# droplet drops a file, plain rsync leaves the previous snapshot intact and the
# new snapshot simply lacks it; every prior snapshot stays whole. Hardlinks make
# unchanged files free, so daily snapshots of ~360MB cost almost nothing.
#
# Destination is OUTSIDE ~/Documents deliberately: anything under Documents
# needs TCC approval, which is what kills LaunchAgent jobs with EX_CONFIG.
#
# Usage: ops/backup.sh
set -euo pipefail

# Host and key are not in the repo: this is public, and an IP plus a port
# policy is free reconnaissance. Put them in ops/local.env (untracked).
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$here/local.env" ] && . "$here/local.env"
HOST=${LAMINAR_HOST:?set LAMINAR_HOST=user@host in ops/local.env (see ops/local.env.example)}
KEY=${LAMINAR_SSH_KEY:?set LAMINAR_SSH_KEY in ops/local.env}
REMOTE=/opt/farseer/data/laminar
DEST_ROOT=${LAMINAR_BACKUP_ROOT:-$HOME/laminar-backup}

stamp=$(date -u +%Y-%m-%dT%H%M%SZ)
dest="$DEST_ROOT/$stamp"
latest="$DEST_ROOT/latest"

mkdir -p "$DEST_ROOT"

echo "--- pulling $REMOTE -> $dest ---"
# Written out longhand rather than with an optional-argument array: macOS ships
# bash 3.2, where expanding an empty array under `set -u` is an error.
if [ -d "$latest" ]; then
  rsync -az --stats -e "ssh -i $KEY" \
        --link-dest="$(cd "$latest" && pwd -P)" "$HOST:$REMOTE/" "$dest/"
else
  rsync -az --stats -e "ssh -i $KEY" "$HOST:$REMOTE/" "$dest/"
fi

ln -sfn "$dest" "$latest"

# --- Verify the copy, don't assume it ------------------------------------
# A backup nobody has read back is a hope, not a backup.
echo "--- verifying ---"
remote_n=$(ssh -i $KEY $HOST "find $REMOTE -type f | wc -l" | tr -d ' ')
local_n=$(find "$dest" -type f | wc -l | tr -d ' ')
echo "files: remote=$remote_n local=$local_n"
[ "$remote_n" = "$local_n" ] || { echo "!! FILE COUNT MISMATCH"; exit 1; }

# The system python3 has no polars; the Farseer venv next door does. Without
# this the readback silently skips and the "verified" line above overstates
# what was actually checked.
PY_BIN=python3
for cand in "$(dirname "$0")/../../farseer/.venv/bin/python" "$HOME/Documents/CC/farseer/.venv/bin/python"; do
  if [ -x "$cand" ] && "$cand" -c "import polars" 2>/dev/null; then PY_BIN="$cand"; break; fi
done

"$PY_BIN" - "$dest" <<'PY'
import sys, pathlib
try:
    import polars as pl
except ImportError:
    print("!! polars unavailable — parquet readback SKIPPED, copy not fully verified")
    sys.exit(0)
root = pathlib.Path(sys.argv[1])
checked = 0
for p in sorted(root.rglob("*.parquet"))[:5] + sorted(root.rglob("*.parquet"))[-5:]:
    pl.read_parquet(p).height  # raises if the file is truncated or corrupt
    checked += 1
print(f"parquet readback OK on {checked} sampled files")
PY

du -sh "$dest"
echo "--- backup verified: $dest ---"
