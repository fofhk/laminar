#!/bin/bash
# Deploy Laminar to the SGP droplet.
#
# Exists because the hand-written rsync line got this wrong for eleven days: a
# trailing slash on `src/` copies its CONTENTS into /opt/farseer/, so the code
# landed at /opt/farseer/laminar/ while the venv imports from
# /opt/farseer/src/laminar/. Deploys reported success and changed nothing.
#
# Two rules encoded here:
#   * every path is explicit — nothing is flattened, nothing outside Laminar's
#     own files on the droplet is touched (the box also runs Farseer/Crescendo)
#   * a deploy is not done until an import on the box proves it landed
#
# Usage: ops/deploy.sh [--check]     (--check verifies only, deploys nothing)
set -euo pipefail

# Host and key are not in the repo: this is public, and an IP plus a port
# policy is free reconnaissance. Put them in ops/local.env (untracked).
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$here/local.env" ] && . "$here/local.env"
HOST=${LAMINAR_HOST:?set LAMINAR_HOST=user@host in ops/local.env (see ops/local.env.example)}
KEY=${LAMINAR_SSH_KEY:?set LAMINAR_SSH_KEY in ops/local.env}
REMOTE=/opt/farseer
SSH="ssh -i $KEY"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_ONLY=${1:-}

cd "$ROOT"

# --- The frozen watchlist is never deployed ------------------------------
# It is frozen for the observation window per the pre-registration; pushing a
# local copy over it would be a pre-registration change, not a deploy. Compare
# and shout, never overwrite.
local_wl=$(md5 -q config/laminar_watchlist.json)
remote_wl=$($SSH $HOST "md5sum $REMOTE/config/laminar_watchlist.json | cut -d' ' -f1")
if [ "$local_wl" != "$remote_wl" ]; then
  echo "!! WATCHLIST DIFFERS from the droplet's frozen copy."
  echo "   local  $local_wl"
  echo "   remote $remote_wl"
  echo "   Not deploying it. Re-freezing the watchlist mid-window is a"
  echo "   pre-registration change and must be a deliberate, recorded decision."
else
  echo "watchlist: matches droplet (frozen, not deployed)"
fi

if [ "$CHECK_ONLY" != "--check" ]; then
  echo "--- deploying ---"
  rsync -az --delete -e "$SSH" src/laminar        "$HOST:$REMOTE/src/"
  rsync -az          -e "$SSH" src/farseer/config.py "$HOST:$REMOTE/src/farseer/"
  rsync -az          -e "$SSH" ops/laminar_daily_report.py ops/notify.py \
                               ops/run_laminar.sh "$HOST:$REMOTE/ops/"
  for t in tests/test_laminar_*.py; do
    rsync -az -e "$SSH" "$t" "$HOST:$REMOTE/tests/"
  done
fi

# --- Verification: an import, not an exit code ---------------------------
echo "--- verifying ---"
$SSH $HOST "$REMOTE/.venv/bin/python - <<'PY'
import laminar.store as s, laminar.shadow as sh, laminar.score as sc
assert s.__file__ == '/opt/farseer/src/laminar/store.py', s.__file__
print('store   ', s.__file__)
print('shadow  ', sh.__file__)
print('books_full writer present:', hasattr(s, 'append_books_full'))
print('score C =', sc.C)
PY"
$SSH $HOST "cd $REMOTE && .venv/bin/python -m pytest tests/test_laminar_*.py -q 2>&1 | tail -3"
echo "--- deploy verified ---"
