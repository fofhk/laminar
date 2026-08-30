# Live reports

One JSON file per report, named `YYYY-MM-DD-<handle>.json`, conforming to
[`../../schema/live_report.v1.json`](../../schema/live_report.v1.json).

**Read [`../../docs/FEEDBACK_PROTOCOL.md`](../../docs/FEEDBACK_PROTOCOL.md)
before adding one.** Reports carry outcomes you computed locally, never venue
market data — the schema rejects anything else, and CI recomputes each report's
conclusions from its own inputs.

```bash
ops/validate_reports.py       # what CI runs
```

This directory is empty of reports until someone runs live. That is the honest
current state of the project.
