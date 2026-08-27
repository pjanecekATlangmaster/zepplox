# ZeppLox

Self-hosted sync of **Amazfit / Zepp** GPS activities into **Livelox**.

Zepp has no official Livelox connection. Zepp *does* officially send workouts to [Intervals.icu](https://intervals.icu). ZeppLox polls Intervals.icu and imports matching routes into the athlete’s Livelox account.

```
Amazfit watch → Zepp → Intervals.icu → ZeppLox → Livelox
```

This project is not affiliated with Zepp Health, Amazfit, Intervals.icu, or Livelox. It does not replace Livelox and does not expose Livelox maps or other people’s routes.

Notable changes are listed in [CHANGELOG.md](CHANGELOG.md).

## What operators and athletes do

1. Link Zepp to Intervals.icu (Zepp app: *3rd-party account linking*, or Intervals.icu Settings).
2. Sign in (or register) with a one-time code sent to your e-mail. There is no password.
3. Paste an Intervals.icu API key and connect Livelox with OAuth.
4. Choose sports and turn sync on. A scheduler on the host (every 30 minutes is enough) imports new GPS activities.

The HTTP service listens on **port 8456**. Point HTTPS (`zepplox.kibos.link` or your hostname) at that port.

**Current milestone:** sign-in, Intervals.icu, Livelox OAuth, sport filters, manual send, and automatic sync every 30 minutes in the container.

Configuration is **only** environment variables. Nothing in this repository is a working deployment: copy `.env.example` to `.env` **on the server**, fill it in, and keep `.env` out of git. GitHub Actions builds and publishes the Docker image; it must not contain hostnames, passwords, API keys, or SMTP settings.

## Configuration

| Variable | Purpose |
|---|---|
| `APP_BASE_URL` | Public HTTPS origin, no trailing slash |
| `PORT` | Listen port (default **8456**) |
| `APP_ENCRYPTION_KEY` | Fernet key; encrypts Intervals keys and Livelox tokens at rest. Generate with the first command below. |
| `SESSION_SECRET` | Signs the login cookie. Generate with the second command below. |
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
| `SYNC_INTERVAL_MINUTES` | Automatic poll interval. `0` = off. The Docker image defaults to **30** |
| `SYNC_LOOKBACK_HOURS` | How far back each poll looks |
| `LOG_RETENTION_DAYS` | Sync log retention (default 7) |

Generate keys on a trusted machine and paste each output into `.env`. Do not swap them.

**`APP_ENCRYPTION_KEY`** (Fernet; encrypts stored Intervals and Livelox secrets):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**`SESSION_SECRET`** (signs the login cookie; any long random string is fine):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Back up `APP_ENCRYPTION_KEY` separately from the database dump. Without it, stored tokens cannot be decrypted. Changing `SESSION_SECRET` only signs everyone out.

## Run locally (Windows)

Same idea as the Livelox map exporter: Python on this machine, no Docker.

```bat
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8456
```

Or `start.bat`. Then open [http://127.0.0.1:8456](http://127.0.0.1:8456). `.env` next to the project must already point at MariaDB and SMTP.

OTP sending is limited to 3 codes per e-mail and 10 per IP in 15 minutes.

## Synology NAS (SSH)

Do **not** build the image on DSM. GitHub Actions publishes `ghcr.io/pjanecekatlangmaster/zepplox:latest`. On the NAS you only download compose files, edit `.env`, and start the container.

MariaDB and SMTP stay outside the container. Point HTTPS (`zepplox.kibos.link` or your hostname) at port **8456**.

### 1. Download

SSH into the NAS and create a folder (example: `/volume1/docker/zepplox`):

```bash
ssh you@nas
sudo mkdir -p /volume1/docker/zepplox
cd /volume1/docker/zepplox
```

Download the three files from `main` (not the Python app — that is inside the image):

```bash
sudo curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/pjanecekATlangmaster/zepplox/main/docker-compose.yml
sudo curl -fsSL -o .env.example https://raw.githubusercontent.com/pjanecekATlangmaster/zepplox/main/.env.example
sudo curl -fsSL -o pull-up.sh https://raw.githubusercontent.com/pjanecekATlangmaster/zepplox/main/pull-up.sh
sudo chmod +x pull-up.sh
```

If `docker compose pull` later returns 401, log into `ghcr.io` with a GitHub token that has `read:packages` (and `repo` if the package is private).

### 2. Edit `.env`

Secrets stay on this host. Never commit `.env`.

```bash
sudo cp .env.example .env
sudo nano .env
```

Set at least `APP_BASE_URL`, `APP_ENCRYPTION_KEY`, `SESSION_SECRET`, `DB_*`, `SMTP_*`, and `LIVELOX_CLIENT_ID`. Generate the two keys with the commands in [Configuration](#configuration). Leave `SYNC_INTERVAL_MINUTES` unset to use the image default of **30**.

### 3. Start

```bash
cd /volume1/docker/zepplox
./pull-up.sh
```

That pulls `:latest` and starts (or recreates) the container. If DSM cannot resolve names (`temporary failure in name resolution`), compose already sets DNS `8.8.8.8` / `1.1.1.1`.

The image runs automatic sync every **30 minutes** unless you set `SYNC_INTERVAL_MINUTES` (use `0` to turn the scheduler off). Users who turned sync off in Settings are skipped. After a container start the first run is about two minutes later. Local `uvicorn` does not schedule sync unless you set the variable.

```bash
sudo docker logs zepplox
sudo docker exec zepplox python -m app.sync
```

Later updates: download the three files again if they changed on GitHub, then run `./pull-up.sh`. After changing `.env`, run `./pull-up.sh` so the container picks up the new values.

## Data stored

- E-mail of each user, settings, and a 7-day import log
- Encrypted Intervals API key and Livelox OAuth tokens
- **Not** stored: GPS files (FIT/GPX are downloaded, posted to Livelox, discarded)

## License

This software is released under the [MIT License](LICENSE). You may use, copy, modify, and distribute it, including commercially, provided the copyright notice and license text are kept.

Use of Livelox, Intervals.icu, and Zepp/Amazfit remains subject to those services’ own terms. This project does not grant any rights to their APIs, trademarks, or map data.
