"""Send a monitor email via direct MX delivery to the ops mailbox.

Usage: notify.py <OK|ALERT> "<subject>"   (HTML body fragment on stdin)
Mirrors the fofhk notify.sh contract: green title on OK, red on ALERT.
No local mail stack — the droplet talks straight to a postfix that is the
final destination for the recipient domain, so no auth/SPF concerns apply.

The endpoint is configuration, not code: set LAMINAR_MAILHOST / MAIL_FROM /
MAIL_TO in the environment (cron sources ops/local.env).
"""

import os
import pathlib
import smtplib
import sys
from email.mime.text import MIMEText

def _setting(name: str) -> str:
    """Env first, then ops/local.env beside this script.

    Loaded here rather than in the callers because there are two of them and
    one is cron running laminar_daily_report.py, which inherits an empty
    environment. A single load point is one thing that can be wrong, not two.
    KeyError on a missing value is deliberate: an unconfigured notifier must
    fail loudly, not send nothing quietly.
    """
    if name in os.environ:
        return os.environ[name]
    for line in (pathlib.Path(__file__).with_name("local.env")).read_text().splitlines():
        line = line.split("#", 1)[0].strip().removeprefix("export ").strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise KeyError(f"{name} is set neither in the environment nor in ops/local.env")


MAILHOST = _setting("LAMINAR_MAILHOST")
MAIL_FROM = _setting("LAMINAR_MAIL_FROM")
MAIL_TO = _setting("LAMINAR_MAIL_TO")


def main() -> int:
    status, subject = sys.argv[1], sys.argv[2]
    body = sys.stdin.read()
    icon = "✅" if status == "OK" else "🔴"
    msg = MIMEText(f"<html><body><h3>{icon} {subject}</h3>{body}</body></html>", "html")
    msg["Subject"] = f"{icon} [farseer] {subject}"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    with smtplib.SMTP(MAILHOST, 25, timeout=30) as s:
        s.send_message(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
