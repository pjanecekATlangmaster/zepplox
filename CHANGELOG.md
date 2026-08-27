# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
ZeppLox is deployed from `main` as `ghcr.io/pjanecekatlangmaster/zepplox:latest`; dates below are ship dates, not semver tags.

## [Unreleased]

- README labels which command generates `APP_ENCRYPTION_KEY` vs `SESSION_SECRET`.
- README Synology SSH setup: download compose files, edit `.env`, then start. Prune leftover notes removed from the README.
- Overview and Settings show when the next automatic sync will run (host interval, or off).
- Re-send to Livelox after the athlete deletes the route: GET the stored import id first. If it still exists, skip. If Livelox returns 404, upload again under a new id.
- Admin page at `/admin` for e-mails in `ADMIN_EMAILS` (user counts, connections, sync runs, recent errors). Admins get a Správa / Admin control next to Settings.
- Automatic sync spreads users across the interval (per-user minute slot) with a short pause between users in the same slot.

## [2026-08-26]

### Added

- Sign-in with a one-time e-mail code (first successful code creates the account).
- GitHub Actions publish of the Docker image to GHCR; NAS only pulls.
- Czech and English landing page (why Strava→Livelox died, Intervals.icu path, privacy).
- Stop sync and delete account in Settings.
- Intervals.icu API key (encrypted at rest) and a 30-day activity preview.
- Livelox OAuth (`routes.import`), connect/disconnect, encrypted tokens.
- Sport filters for automatic transfer, and manual send of selected GPS activities.
- Automatic Intervals.icu → Livelox poll every 30 minutes inside the container.
- `pull-up.sh` on the NAS: pull `:latest`, recreate the container, prune the unused previous image.

### Changed

- Landing and settings copy for Intervals.icu, Livelox, and privacy.
- Header shows only the other language (English or Česky). GitHub is in the footer only.
- Notable changes are listed in `CHANGELOG.md`.

### Fixed

- `pull-up.sh` Unix line endings so DSM `sh` can run it.
- GPS detection uses Intervals.icu `stream_types` (`latlng`) and GPX/TCX `file_type`. Uploaded GPX files were marked as without GPS because `has_map` / start coordinates are often empty in the activity list.
- Activities previously skipped as “no GPS” are retried on the next automatic sync.
