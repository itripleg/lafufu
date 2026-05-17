# Lafufu

Composable robot platform — Pi-hosted servo/voice agent with web admin.

## Status: Phase 0 (foundation)

See [`docs/superpowers/specs/`](docs/superpowers/specs/) for design docs and [`docs/superpowers/plans/`](docs/superpowers/plans/) for implementation plans.

## Quick start (dev)

```bash
uv sync
./scripts/dev_run_all.sh
```

## Architecture

4 Python services on a NATS bus + SolidJS admin/face SPA. See spec for full detail.
