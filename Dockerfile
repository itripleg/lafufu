# Lafufu control service — UI/UX deployment image.
#
# Runs ONLY the control service (FastAPI API + built SolidJS SPA: Studio,
# frames/expressions, settings, chat UI). The agent (voice), animator (servos)
# and printer are hardware-bound and intentionally excluded — this image is for
# UX/UI work and a shareable remote link. NATS is provided as a sibling
# container (see docker-compose.prod.yml); control connects to it on startup.
#
# Lean by design: installs only the `lafufu-control` package + its deps
# (no torch/openwakeword), and reuses the SPA bundle already committed under
# packages/control/src/lafufu_control/static (rebuild with `cd web && npm run
# build` before building the image to pick up frontend changes).
FROM python:3.13-slim

# uv for fast, locked installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    LAFUFU_DATA_DIR=/data \
    LAFUFU_NATS_URL=nats://nats:4222 \
    LAFUFU_CONTROL_PORT=8080 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Install control's closure + lafufu-animator (control's animator router imports
# lafufu_animator.pose at module load; animator only pulls dynamixel-sdk + numpy,
# no torch). The agent package stays out — control imports it lazily, so only the
# device-probe endpoints degrade, and it would drag in torch/openwakeword.
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
RUN uv sync --package lafufu-control --package lafufu-animator --no-dev --frozen

# Bundled assets: sprite gallery + letterheads + fonts served by the API.
COPY assets/ assets/

EXPOSE 8080
CMD ["python", "-m", "lafufu_control"]
