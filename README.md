# ZeppLox

Self-hosted sync of **Amazfit / Zepp** GPS activities into **Livelox**.

Zepp has no official Livelox connection. Zepp *does* officially send workouts to [Intervals.icu](https://intervals.icu). ZeppLox polls Intervals.icu and imports matching routes into the athlete’s Livelox account.

```
Amazfit watch → Zepp → Intervals.icu → ZeppLox → Livelox
```

This project is not affiliated with Zepp Health, Amazfit, Intervals.icu, or Livelox. It does not replace Livelox and does not expose Livelox maps or other people’s routes.

## What operators and athletes do

1. Link Zepp to Intervals.icu (Zepp app: *3rd-party account linking*, or Intervals.icu Settings).
2. Sign in to ZeppLox with a one-time code sent to an allowed e-mail address (no password).
3. Paste an Intervals.icu API key and connect Livelox with OAuth.
4. Choose sports and turn sync on. A scheduler on the host (every 30 minutes is enough) imports new GPS activities.

The HTTP service listens on **port 8456**. Point HTTPS (`zepplox.kibos.link` or your hostname) at that port.

Configuration is **only** environment variables. Nothing in this repository is a working deployment: copy `.env.example` to `.env` **on the server**, fill it in, and keep `.env` out of git. GitHub Actions only builds the Docker image and must not contain hostnames, passwords, API keys, or SMTP settings.

## Configuration

| Variable | Purpose |
|---|---|
| `APP_BASE_URL` | Public HTTPS origin, no trailing slash |
| `PORT` | Listen port (default **8456**) |
| `APP_ENCRYPTION_KEY` | Fernet key; encrypts Intervals keys and Livelox tokens at rest |
| `SESSION_SECRET` | Signs the login cookie |
| `DB_*` | MariaDB connection (empty database; tables are created at startup) |
| `SMTP_*` | Outbound mail for OTP. Microsoft 365 IP relay: host `*.mail.protection.outlook.com`, port 25, STARTTLS, no password |
| `SMTP_FROM` | Must be an accepted domain on that tenant |
| `ALLOWED_EMAILS` | Who may log in (comma-separated) |
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

## Run (Docker)

```bash
cp .env.example .env
# edit .env on this host only
docker compose up -d --build
```

Point HTTPS at port **8456**. Schedule on the NAS (every 30 minutes):

```bash
docker exec zepplox python -m app.sync
```

Livelox OAuth needs a publicly reachable HTTPS callback. Sync itself only makes outbound HTTPS.

## Data stored

- E-mail of each user, settings, and a 7-day import log
- Encrypted Intervals API key and Livelox OAuth tokens
- **Not** stored: GPS files (FIT/GPX are downloaded, posted to Livelox, discarded)

## License

This software is released under the [MIT License](LICENSE). You may use, copy, modify, and distribute it, including commercially, provided the copyright notice and license text are kept.

Use of Livelox, Intervals.icu, and Zepp/Amazfit remains subject to those services’ own terms. This project does not grant any rights to their APIs, trademarks, or map data.
