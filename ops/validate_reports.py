#!/usr/bin/env python3
"""Validate contributor live reports against schema/live_report.v1.json.

Run by CI on every PR, so a report that does not conform never merges. Three
checks, in order of how much they matter:

1. **Schema.** `additionalProperties: false` is what actually enforces "send
   outcomes, not market data" — a book snapshot has nowhere to go.
2. **Arithmetic.** A report carries both its inputs and its conclusion, so the
   conclusion can be recomputed rather than believed. A submitter cannot assert
   a denominator or a verdict their own numbers do not support.
3. **Wallet addresses.** The schema keeps them out of structured fields by
   pattern, but `notes` and `summary` are free text. A 40-hex-character address
   is almost never something a reporter meant to publish.

Usage: ops/validate_reports.py [path ...]      (default: every report + the example)
"""

import json
import pathlib
import re
import sys

import jsonschema

from laminar.feedback import bracket_verdict, implied_denominator

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema" / "live_report.v1.json"
# 40 hex characters exactly — a condition_id is 64 and will not match.
WALLET = re.compile(r"0x[0-9a-fA-F]{40}\b")
TOLERANCE = 0.005  # 0.5%; enough slack for a reporter rounding their own figures


def check(path: pathlib.Path, schema: dict) -> list[str]:
    raw = path.read_text()
    problems = [f"wallet-shaped literal {m.group()[:6]}...: remove it" for m in WALLET.finditer(raw)]

    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        return problems + [f"not valid JSON: {exc}"]

    for err in sorted(jsonschema.Draft202012Validator(schema).iter_errors(report), key=str):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        problems.append(f"{where}: {err.message}")
    if problems:
        # Recomputing against a report that failed the schema would just produce
        # KeyErrors dressed up as findings.
        return problems

    for i, obs in enumerate(report["observations"]):
        if obs["kind"] != "denominator_measurement":
            continue
        at = f"observations/{i}"
        recomputed = implied_denominator(obs["mine_qmin"], obs["reward_paid_usdc"], obs["pool_usdc"])
        claimed = obs["d_implied"]
        if abs(recomputed - claimed) > TOLERANCE * max(recomputed, 1.0):
            problems.append(f"{at}/d_implied: claimed {claimed:g}, inputs give {recomputed:g}")
            continue
        expected = bracket_verdict(recomputed, obs["d_min"], obs["d_max"])
        if obs["verdict"] not in (expected, "undetermined"):
            problems.append(f"{at}/verdict: claimed {obs['verdict']!r}, inputs give {expected!r}")
    return problems


def _shown(path: pathlib.Path) -> str:
    """Repo-relative where possible — an explicitly passed path may be elsewhere."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str]) -> int:
    schema = json.loads(SCHEMA.read_text())
    paths = [pathlib.Path(a) for a in argv] or sorted(
        [*(ROOT / "contrib" / "reports").glob("*.json"), ROOT / "schema" / "example_live_report.json"]
    )
    failed = 0
    for path in paths:
        problems = check(path, schema)
        if problems:
            failed += 1
            print(f"FAIL {_shown(path)}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"ok   {_shown(path)}")
    if failed:
        print(f"\n{failed} report(s) rejected. See docs/FEEDBACK_PROTOCOL.md.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
