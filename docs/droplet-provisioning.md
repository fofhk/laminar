# Farseer droplet — provisioning checklist

> **STATUS 2026-06-14: DONE.** `root@DROPLET_IP` (SGP1, 1 vCPU,
> hostname `Farseer`, Ubuntu 24.04, Python 3.12). SSH key
> `~/.ssh/farseer_ed25519` ONLY (fofhk key removed). Hardened: ufw
> 22-only, fail2ban, no password auth, unattended-upgrades. Code at
> `/opt/farseer` (rsync'd from Mac; venv built; 4 tests pass). Mac
> `data/` migrated and merging verified. Cron: collector hourly at :07
> UTC with alert wrapper; daily report 21:00 UTC (07:00 AEST) to
> MAIL_TO. Mail = direct MX to MAILHOST_IP:25; the farseer IP
> is whitelisted in rspamd (`/etc/rspamd/local.d/settings.conf` on the
> mail box). Remaining: nightly off-box backup of `data/` (interim:
> rsync pull to Mac during daily wrap-ups).
> Deploy command: `rsync -az -e "ssh -i ~/.ssh/farseer_ed25519" src ops config pyproject.toml root@DROPLET_IP:/opt/farseer/`
>
> **CORRECTED 2026-08-25 — the previous form was silently a no-op.** It read
> `... src/ ops/ config/ pyproject.toml ...`; the trailing slashes make rsync
> copy each directory's *contents* into `/opt/farseer/`, so `src/laminar/`
> landed at `/opt/farseer/laminar/` while the venv's editable install resolves
> `laminar` from `/opt/farseer/src/laminar/`. Deploys appeared to succeed and
> changed nothing. It also left shadow packages at `/opt/farseer/laminar/` and
> `/opt/farseer/farseer/` that took import precedence for anything run with
> `cwd=/opt/farseer` — both verified byte-identical to `src/`, backed up to
> `backups/shadow_pkgs_20260825.tgz`, and removed. Drop the source-side
> trailing slashes and the paths are preserved.
>
> Verify a deploy landed, don't assume:
> `ssh … '/opt/farseer/.venv/bin/python -c "import laminar.store as s; print(s.__file__)"'`

**Decision (2026-06-13):** dedicated DO droplet, separate from the
fofhk/iKnow/mail box (`MAILHOST_IP`). Rationale: this machine will hold a
funded wallet key at Stage 2 → minimal attack surface (no nginx/PHP/mail),
independent blast radius, free experimentation without endangering the
investor-facing site.

## Create (user action, DO control panel)

- [ ] Droplet: Ubuntu LTS, **2 vCPU / 4 GB** (resize later if backtests need it)
- [ ] Region: **SGP1** (closest to Hyperliquid's Tokyo infra; SYD also fine)
- [ ] SSH key only — generate a NEW key, do not reuse `fofhk_ed25519`:
      `ssh-keygen -t ed25519 -f ~/.ssh/farseer_ed25519 -C farseer`
- [ ] Hostname: `farseer`

## Harden (first login)

- [ ] `apt update && apt upgrade`; enable `unattended-upgrades`
- [ ] ufw: allow 22/tcp only, default deny incoming (no web server, ever)
- [ ] sshd: `PasswordAuthentication no`, `PermitRootLogin prohibit-password`
- [ ] fail2ban for sshd

## Deploy

- [ ] `python3 -m venv /opt/farseer/venv` — one-venv-per-service rule;
      OS Python upgrade ⇒ rebuild venv (lesson from fofhk 2026-05-27 freeze)
- [ ] Code via git (read-only deploy key, same pattern as fofhk-front)
- [ ] `FARSEER_ROOT=/opt/farseer` in the service environment
- [ ] Cron: `python -m farseer.data.collect` **hourly** (1m depth is ~3.5
      days, so even daily would not lose bars; hourly keeps gaps short and
      catches failures fast). Wrap in a `run_job.sh`-style de-silencer:
      non-zero exit OR error-signature in output ⇒ immediate alert email.
- [ ] Alerting: send via the existing mail droplet (authenticated
      submission to the ops MX) — do NOT install postfix here.
- [ ] Daily status report (collector freshness: newest 1m bar age per
      symbol, disk, venv import smoke test) — green/red email, absence of
      the email is itself the alarm. Reuse fofhk `daily_report.py` shape.
- [ ] Nightly off-box backup of `data/` (the minute bars are
      irreplaceable — REST cannot backfill them). rsync to the Mac or DO
      Spaces; verify restore once.

## Explicitly NOT on this box

nginx · PHP · postfix/dovecot · PostgreSQL server · other projects' code

> **Note 2026-08-14 — the rule stands; one narrow exception is recorded, not a
> relaxation.** `laminar.collect` (cron: `books` every minute, `markets` hourly,
> via `ops/run_laminar.sh`) now runs here. It is admitted on the ground that it
> holds **no keys and cannot place an order** — `src/laminar/clob.py` implements
> public read endpoints only, by construction. It is testing and data collection
> for a prediction-market-maker strategy, not that strategy running.
>
> The rule's actual purpose — keeping a box that will hold a funded wallet key
> to minimal attack surface — is untouched by a read-only sampler, and it is
> exactly why **Laminar's own execution stage will NOT run here**. That needs a
> second funded key (Polygon) and a permitted jurisdiction; both argue for its
> own droplet (AMS3 is the only DO region where Polymarket's API is
> unrestricted). SGP1 stays single-purpose for the funded Farseer key.
>
> Strictly speaking Laminar is not Farseer — Farseer is analysis and decision
> support, and there is no plan to take it high-frequency. See
> `laminar_20260814_preregistration.md`.
