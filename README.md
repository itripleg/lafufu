# Lafufu

Composable robot platform — Pi-hosted servo/voice agent with web admin.

## Status: Phase 0 (foundation)

See [`docs/superpowers/specs/`](docs/superpowers/specs/) for design docs and [`docs/superpowers/plans/`](docs/superpowers/plans/) for implementation plans.

## Quick start (dev)

There is no single dev-runner script. Sync deps, then start each service in its
own terminal:

```bash
uv sync --all-packages

# Python services (each in its own terminal):
uv run python -m lafufu_control
uv run python -m lafufu_agent
uv run python -m lafufu_animator
uv run python -m lafufu_printer

# SPA dev server (hot-reload, proxied to control on :8080):
cd web && npm run dev
```

NATS must be running before the services start (`nats-server` on default port
4222). On the Pi it runs as a systemd unit; locally, install and run it
manually.

## Deploy to the Pi

SSH to the Pi, then run the idempotent updater:

```bash
ssh pi@<pi-ip>
cd /srv/lafufu && ./deploy/deploy.sh
```

`deploy.sh` fetches, checks for a dirty tree (exits with a clear error rather
than discarding local changes), pulls with `--ff-only`, syncs Python deps,
rebuilds the web SPA only if `web/` changed, and restarts the four application
services one-at-a-time (matching the passwordless sudoers entries in
`deploy/sudoers/lafufu-services`). It also runs a health check and warns on any
service that didn't come up.

For a full first-time install see `deploy/install.sh` (run as root on the Pi).

## Architecture

4 Python services on a NATS bus + SolidJS admin/face SPA. See spec for full detail.

## License

MIT © 2026 Joshua Bell, Motherhaven LLC. See [LICENSE](LICENSE).
