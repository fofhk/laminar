"""Scheduled macro events, so an expected news-driven move isn't read as an anomaly.

Nearly every market in the Stage-1 watchlist is deliberately low-catalyst
("who is the next James Bond"), with one exception: the Fed rate markets, which
are the only ones carrying a *scheduled* catalyst. Without this table, a DRIFT
or BAND flag on an FOMC day looks identical to a genuine surprise; with it, the
daily report can say "expected".

Sources (fetched 2026-08-14):
  FOMC  — federalreserve.gov/monetarypolicy/fomccalendars.htm
  CPI   — BLS schedule (bls.gov 403s to scripts; taken from a mirror of the
          BLS calendar, so treat as good-but-secondhand and re-check if a
          date ever matters for a decision rather than a label).

FOMC decisions land on the SECOND day of each two-day meeting, ~14:00 ET.
CPI releases at 08:30 ET. Stored as UTC dates only — intraday timing is not
modelled, because the daily report's unit is a UTC day.
"""

from datetime import date

# (decision date, has Summary of Economic Projections / dot plot)
FOMC_2026: list[tuple[date, bool]] = [
    (date(2026, 1, 28), False),
    (date(2026, 3, 18), True),
    (date(2026, 4, 29), False),
    (date(2026, 6, 17), True),
    (date(2026, 7, 29), False),
    (date(2026, 9, 16), True),
    (date(2026, 10, 28), False),
    (date(2026, 12, 9), True),
]

FOMC_2027: list[tuple[date, bool]] = [
    (date(2027, 1, 27), False),
    (date(2027, 3, 17), True),
    (date(2027, 4, 28), False),
    (date(2027, 6, 9), True),
    (date(2027, 7, 28), False),
    (date(2027, 9, 15), True),
    (date(2027, 10, 27), False),
    (date(2027, 12, 8), True),
]

# (release date, reference month covered)
CPI_2026: list[tuple[date, str]] = [
    (date(2026, 8, 12), "July 2026"),
    (date(2026, 9, 11), "August 2026"),
    (date(2026, 10, 14), "September 2026"),
    (date(2026, 11, 10), "October 2026"),
    (date(2026, 12, 10), "November 2026"),
]


def events_on(day: date) -> list[str]:
    """Human-readable scheduled events on a given UTC date ([] if none)."""
    out = []
    for d, sep in FOMC_2026 + FOMC_2027:
        if d == day:
            out.append("FOMC decision" + (" + SEP/dot plot" if sep else ""))
    for d, ref in CPI_2026:
        if d == day:
            out.append(f"CPI release ({ref} data)")
    return out


def next_events(after: date, limit: int = 3) -> list[tuple[date, str]]:
    """Upcoming scheduled events, soonest first — so the daily report can warn
    ahead of a catalyst rather than only explaining one after the fact."""
    upcoming: list[tuple[date, str]] = []
    for d, sep in FOMC_2026 + FOMC_2027:
        if d > after:
            upcoming.append((d, "FOMC decision" + (" + SEP/dot plot" if sep else "")))
    for d, ref in CPI_2026:
        if d > after:
            upcoming.append((d, f"CPI release ({ref} data)"))
    return sorted(upcoming)[:limit]
