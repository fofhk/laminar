#!/bin/bash
# Fail if anything address-shaped reached the working tree.
#
# This repository was published by rewriting history: earlier commits carried
# the collector's address, and a worklog carried the development machine's home
# egress IP and ISP. Both were missed by a keyword pass, because a keyword pass
# only finds the values you already remember. This matches on SHAPE instead —
# any IPv4 literal, any e-mail address — and deliberately does NOT name the
# values it is protecting, since writing them here would put them back.
#
# One implementation, two callers: CI runs it on every push, and
# .git/hooks/pre-push runs it before anything leaves the machine. Install the
# hook with:  ops/leak_scan.sh --install-hook
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ "${1:-}" = "--install-hook" ]; then
  printf '#!/bin/bash\nexec "$(git rev-parse --show-toplevel)/ops/leak_scan.sh"\n' > .git/hooks/pre-push
  chmod +x .git/hooks/pre-push
  echo "installed .git/hooks/pre-push"
  exit 0
fi

# 203.0.113.0/24 (RFC 5737) and .invalid (RFC 2606) are reserved for docs.
# documentation; ops/local.env.example is allowed to use them.
# Scans TRACKED files only — git ls-files is exactly the set that would be
# published, and ops/local.env (untracked, holding the real values) must not
# register as a finding.
hits=$(git ls-files -z \
       | grep -zvE '^(LICENSE|ops/leak_scan\.sh|ops/local\.env\.example)$' \
       | xargs -0 grep -InE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
       | grep -vE '203\.0\.113\.|@example\.invalid' \
       || true)

if [ -n "$hits" ]; then
  echo "$hits"
  echo
  echo "An address-shaped literal reached the tree."
  echo "Host, key and mail endpoint belong in ops/local.env (untracked)."
  echo "If you are quoting a leak as evidence, write a placeholder, not the value."
  exit 1
fi
echo "leak scan: clean"
