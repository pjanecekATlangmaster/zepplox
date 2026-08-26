# ZeppLox

Self-hosted sync of **Amazfit / Zepp** GPS activities into **Livelox**.

Zepp has no official Livelox connection. Zepp *does* officially send workouts to [Intervals.icu](https://intervals.icu). ZeppLox polls Intervals.icu and imports matching routes into the athlete’s Livelox account.

```
Amazfit watch → Zepp → Intervals.icu → ZeppLox → Livelox
```

This project is not affiliated with Zepp Health, Amazfit, Intervals.icu, or Livelox. It does not replace Livelox and does not expose Livelox maps or other people’s routes.

## What operators and athletes do

1. Link Zepp to Intervals.icu (Zepp app: *3rd-party account linking*, or Intervals.icu Settings).
2. Sign in (or register) with a one-time code sent to your e-mail. There is no password.
3. Paste an Intervals.icu API key and connect Livelox with OAuth.
4. Choose sports and turn sync on. A scheduler on the host (every 30 minutes is enough) imports new GPS activities.

The HTTP service listens on **port 8456**. Point HTTPS (`zepplox.kibos.link` or your hostname) at that port.

**Current milestone:** sign-in with a one-time e-mail code. Intervals.icu and Livelox import are not wired yet.

Configuration is **only** environment variables. Nothing in this repository is a working deployment: copy `.env.example` to `.env` **on the server**, fill it in, and keep `.env` out of git. GitHub Actions builds and publishes the Docker image; it must not contain hostnames, passwords, API keys, or SMTP settings.

## Configuration

| Variable | Purpose |
|---|---|
| `APP_BASE_URL` | Public HTTPS origin, no trailing slash |
| `PORT` | Listen port (default **8456**) |
| `APP_ENCRYPTION_KEY` | Fernet key; encrypts Intervals keys and Livelox tokens at rest |
| `SESSION_SECRET` | Signs the login cookie |
| `DB_*` | Existing MariaDB (empty database; tables are created at startup) |
| `SMTP_HOST` / `SMTP_PORT` | Mail server |
| `SMTP_ENCRYPTION` | `starttls` (default), `ssl` (port 465), or `none` |
| `SMTP_USER` / `SMTP_PASSWORD` | Optional. Empty = no AUTH (IP relay). If `SMTP_USER` is set, password is required |
| `SMTP_FROM` | Envelope From; must be accepted by that server |
| `OTP_MAX_PER_WINDOW` | Max OTP e-mails per address per window (default 3) |
| `OTP_WINDOW_SECONDS` | Rate-limit window (default 900 = 15 minutes) |
| `OTP_MAX_PER_IP` | Max OTP e-mails per client IP per window (default 10) |
| `LIVELOX_CLIENT_ID` | From [info@livelox.com](mailto:info@livelox.com); user-delegated access, scope `routes.import` |
| `LIVELOX_REDIRECT_URI` | Optional; defaults to `{APP_BASE_URL}/oauth/livelox/callback` |
| `SYNC_LOOKBACK_HOURS` | How far back each poll looks |
| `LOG_RETENTION_DAYS` | Sync log retention (default 7) |

Generate keys on a trusted machine:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Back up `APP_ENCRYPTION_KEY` separately from the database dump. Without it, stored tokens cannot be decrypted.

## Run locally (Windows)

Same idea as the Livelox map exporter: Python on this machine, no Docker.

```bat
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8456
```

Or `start.bat`. Then open [http://127.0.0.1:8456](http://127.0.0.1:8456). `.env` next to the project must already point at MariaDB and SMTP.

OTP sending is limited to 3 codes per e-mail and 10 per IP in 15 minutes.

## Synology NAS

Same path as the Livelox map exporter: **do not build on DSM**. GitHub Actions publishes `ghcr.io/pjanecekatlangmaster/zepplox:latest`. Container Manager only pulls and starts.

```bash
cp .env.example .env
# set APP_ENCRYPTION_KEY, SESSION_SECRET, DB_*, SMTP_* on this host
docker compose pull
docker compose up -d
```

Point HTTPS (`zepplox.kibos.link`) at port **8456**. MariaDB and SMTP stay outside the container (the NAS MariaDB you already created).

If DSM cannot resolve names (`temporary failure in name resolution`), compose already sets DNS `8.8.8.8` / `1.1.1.1`. Log into registry `ghcr.io` in Container Manager with a GitHub token that has `read:packages` (and `repo` if the package is private), same as for Livelox.

Do not schedule `app.sync` until the Intervals → Livelox path exists.

## Data stored

- E-mail of each user, settings, and a 7-day import log
- Encrypted Intervals API key and Livelox OAuth tokens
- **Not** stored: GPS files (FIT/GPX are downloaded, posted to Livelox, discarded)

## License

This software is released under the [MIT License](LICENSE). You may use, copy, modify, and distribute it, including commercially, provided the copyright notice and license text are kept.

Use of Livelox, Intervals.icu, and Zepp/Amazfit remains subject to those services’ own terms. This project does not grant any rights to their APIs, trademarks, or map data.
