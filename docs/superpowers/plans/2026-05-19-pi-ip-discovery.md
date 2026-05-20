# Pi IP Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Pi's LAN IP discoverable two ways — an always-on Bluetooth name broadcast and an on-demand "what's your IP" voice trigger that prints and speaks the address.

**Architecture:** A shared `netinfo` helper finds the primary LAN IP. A standalone root systemd service (`lafufu-btcast`) keeps that IP in the Bluetooth adapter's discoverable name while online and hides the adapter while offline. Inside the agent's voice pipeline, a pre-LLM intent intercept answers IP queries directly — publishing a print job and speaking the address — without calling Ollama.

**Tech Stack:** Python 3.13, uv workspace, pytest (`asyncio_mode = auto`), NATS (pydantic-typed messages), bash + systemd + BlueZ `bluetoothctl`.

**Spec:** `docs/superpowers/specs/2026-05-19-pi-ip-discovery-design.md`

**Context:** Implemented in the `worktree-pi-ip-discovery` worktree. Baseline before this plan: **141 tests passing**. All commands run from the worktree root.

---

## File Structure

**New files:**
- `packages/shared/src/lafufu_shared/netinfo.py` — primary-LAN-IP helper + `python -m` entry point
- `packages/shared/tests/test_netinfo.py` — tests for `netinfo`
- `packages/agent/src/lafufu_agent/intents.py` — IP-query phrase matching + slip/answer formatting
- `packages/agent/tests/test_intents.py` — tests for `intents`
- `deploy/lafufu-btcast.sh` — the Bluetooth broadcaster loop
- `deploy/systemd/lafufu-btcast.service` — systemd unit for the broadcaster

**Modified files:**
- `packages/shared/src/lafufu_shared/__init__.py` — export the `netinfo` submodule
- `packages/shared/src/lafufu_shared/schemas.py` — add `"system"` to `AgentReply.source`
- `packages/shared/tests/test_schemas.py` — test the new `source` value
- `packages/agent/src/lafufu_agent/pipeline.py` — intent intercept in `run_one_cycle`
- `packages/agent/tests/test_pipeline.py` — test the intercept
- `deploy/install.sh` — install `bluez`, enable `lafufu-btcast`, make the adapter permanently discoverable

---

## Task 1: `netinfo` — shared primary-LAN-IP helper

**Files:**
- Create: `packages/shared/src/lafufu_shared/netinfo.py`
- Create: `packages/shared/tests/test_netinfo.py`
- Modify: `packages/shared/src/lafufu_shared/__init__.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/shared/tests/test_netinfo.py`:

```python
import socket

from lafufu_shared.netinfo import primary_lan_ip


def test_returns_routable_ipv4(monkeypatch):
    class _FakeSock:
        def connect(self, addr):
            pass

        def getsockname(self):
            return ("192.168.1.42", 54321)

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: _FakeSock())
    assert primary_lan_ip() == "192.168.1.42"


def test_returns_none_when_offline(monkeypatch):
    class _DeadSock:
        def connect(self, addr):
            raise OSError("Network is unreachable")

        def getsockname(self):
            raise AssertionError("must not be reached when offline")

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: _DeadSock())
    assert primary_lan_ip() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/shared/tests/test_netinfo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lafufu_shared.netinfo'`

- [ ] **Step 3: Write the implementation**

Create `packages/shared/src/lafufu_shared/netinfo.py`:

```python
"""Network introspection: the Pi's own primary LAN IP address.

Shared by the agent's "what's your IP" voice intent and the Bluetooth
broadcaster (deploy/lafufu-btcast.sh), which calls the module entry
point: ``python -m lafufu_shared.netinfo``.
"""

import socket


def primary_lan_ip() -> str | None:
    """Return the primary LAN IPv4 address, or None when offline.

    Opens a UDP socket and 'connects' it toward a public address so the
    OS picks the outbound interface; no packets are actually sent. Reads
    back that interface's local address.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


if __name__ == "__main__":
    _ip = primary_lan_ip()
    print(_ip if _ip else "")
```

- [ ] **Step 4: Export the submodule**

Modify `packages/shared/src/lafufu_shared/__init__.py` to its full new content:

```python
from . import (
    base_service,
    logging_setup,
    nats_helper,
    netinfo,
    prompts,
    schemas,
    settings,
    topics,
)

__all__ = [
    "base_service",
    "logging_setup",
    "nats_helper",
    "netinfo",
    "prompts",
    "schemas",
    "settings",
    "topics",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/shared/tests/test_netinfo.py -v`
Expected: PASS — 2 passed

- [ ] **Step 6: Lint**

Run: `uv run ruff check --fix packages/shared && uv run ruff format packages/shared`
Expected: no remaining errors.

- [ ] **Step 7: Commit**

```bash
git add packages/shared/src/lafufu_shared/netinfo.py packages/shared/tests/test_netinfo.py packages/shared/src/lafufu_shared/__init__.py
git commit -m "feat(shared): add netinfo.primary_lan_ip helper"
```

---

## Task 2: Allow `AgentReply.source = "system"`

The IP intent's spoken reply is neither an LLM reply nor operator puppetry, so `AgentReply.source` needs a third value.

**Files:**
- Modify: `packages/shared/src/lafufu_shared/schemas.py:37-43`
- Modify: `packages/shared/tests/test_schemas.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/shared/tests/test_schemas.py`:

```python
def test_agent_reply_source_system_is_valid():
    r = schemas.AgentReply(text="hi", emotion="neutral", source="system")
    assert r.source == "system"


def test_agent_reply_source_rejects_unknown():
    with pytest.raises(ValidationError):
        schemas.AgentReply(text="hi", emotion="neutral", source="bogus")
```

- [ ] **Step 2: Run the tests to verify the first fails**

Run: `uv run pytest packages/shared/tests/test_schemas.py::test_agent_reply_source_system_is_valid -v`
Expected: FAIL — `ValidationError` ("system" not yet allowed).

- [ ] **Step 3: Write the implementation**

In `packages/shared/src/lafufu_shared/schemas.py`, replace the `AgentReply` class body (currently lines 37-43):

```python
class AgentReply(BaseModel):
    text: str
    emotion: Emotion
    # Where this reply originated. 'llm' = generated from a chat cycle,
    # 'puppet' = direct text-to-speech via speak_text intent (operator
    # typed exactly what Lafufu should say).
    source: Literal["llm", "puppet"] = "llm"
```

with:

```python
class AgentReply(BaseModel):
    text: str
    emotion: Emotion
    # Where this reply originated. 'llm' = generated from a chat cycle,
    # 'puppet' = direct text-to-speech via speak_text intent (operator
    # typed exactly what Lafufu should say), 'system' = a built-in intent
    # answered directly by the agent (e.g. the "what's your IP" query).
    source: Literal["llm", "puppet", "system"] = "llm"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/shared/tests/test_schemas.py -v`
Expected: PASS — all schema tests pass, including both new ones.

- [ ] **Step 5: Commit**

```bash
git add packages/shared/src/lafufu_shared/schemas.py packages/shared/tests/test_schemas.py
git commit -m "feat(shared): allow AgentReply.source=\"system\""
```

---

## Task 3: `intents` — IP-query matching and answer formatting

Three pure functions: detect an IP query, render the printed slip, render the spoken line.

**Files:**
- Create: `packages/agent/src/lafufu_agent/intents.py`
- Create: `packages/agent/tests/test_intents.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/agent/tests/test_intents.py`:

```python
from datetime import datetime

from lafufu_agent.intents import build_ip_slip, match_ip_intent, spoken_ip_answer


def test_match_plain_question():
    assert match_ip_intent("what's your IP address")


def test_match_with_wake_words_and_punctuation():
    assert match_ip_intent("Hey Lafufu, what is your IP address?")


def test_match_network_address_phrasing():
    assert match_ip_intent("tell me your network address")


def test_no_match_on_ordinary_chat():
    assert not match_ip_intent("tell me a fortune about my future")


def test_no_match_on_empty():
    assert not match_ip_intent("")


def test_slip_contains_ip_hostname_and_admin_url():
    slip = build_ip_slip("192.168.1.42", "lafufu", datetime(2026, 5, 19, 14, 32))
    assert "192.168.1.42" in slip
    assert "lafufu" in slip
    assert "http://192.168.1.42:8080/admin" in slip
    assert "2026-05-19 14:32" in slip


def test_spoken_answer_with_ip():
    line = spoken_ip_answer("192.168.1.42")
    assert "192.168.1.42" in line
    assert "printed" in line


def test_spoken_answer_offline():
    line = spoken_ip_answer(None)
    assert "network connection" in line
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/agent/tests/test_intents.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lafufu_agent.intents'`

- [ ] **Step 3: Write the implementation**

Create `packages/agent/src/lafufu_agent/intents.py`:

```python
"""Built-in voice intents the agent answers directly, without the LLM.

Currently just the "what's your IP address" query: the agent cannot ask
Ollama for its own IP, so the pipeline intercepts the transcript, looks
the IP up itself, and both prints and speaks it.
"""

from datetime import datetime

# Phrases that mark a "what is your IP" request. Lowercased substring
# match against the transcript — deliberately small and easy to tune.
_IP_INTENT_PHRASES = (
    "ip address",
    "what's your ip",
    "whats your ip",
    "your ip",
    "network address",
)


def match_ip_intent(text: str) -> bool:
    """True when the transcript is asking for the Pi's IP address."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in _IP_INTENT_PHRASES)


def build_ip_slip(ip: str, hostname: str, now: datetime) -> str:
    """Render the receipt-printer slip shown when the IP intent fires."""
    return (
        "   LAFUFU · NETWORK\n"
        f"hostname  {hostname}\n"
        f"IP        {ip}\n"
        f"admin     http://{ip}:8080/admin\n"
        f"          {now:%Y-%m-%d %H:%M}\n"
    )


def spoken_ip_answer(ip: str | None) -> str:
    """The line Lafufu speaks aloud for the IP intent."""
    if ip is None:
        return "I can't find a network connection right now."
    return f"My IP address is {ip}. I have printed it for you."
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/agent/tests/test_intents.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Lint**

Run: `uv run ruff check --fix packages/agent && uv run ruff format packages/agent`
Expected: no remaining errors.

- [ ] **Step 6: Commit**

```bash
git add packages/agent/src/lafufu_agent/intents.py packages/agent/tests/test_intents.py
git commit -m "feat(agent): add IP-query intent matching + slip formatting"
```

---

## Task 4: Pipeline intercept — answer the IP query directly

In `run_one_cycle`, after the transcript is published and before the LLM is called, intercept an IP query: print a slip and speak the address, then return.

**Files:**
- Modify: `packages/agent/src/lafufu_agent/pipeline.py`
- Modify: `packages/agent/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/agent/tests/test_pipeline.py`:

```python
async def test_ip_intent_prints_and_speaks_without_llm(nats_server, monkeypatch):
    """A 'what's your IP' utterance must print a slip and speak the IP,
    and must NOT call the LLM."""
    import nats
    from lafufu_shared.testing import FakePiper

    monkeypatch.setattr(
        "lafufu_shared.netinfo.primary_lan_ip", lambda: "192.168.1.42"
    )

    nc = await nats.connect(nats_server)
    prints: list[schemas.PrinterIntentPrintText] = []
    replies: list[schemas.AgentReply] = []

    async def cb_print(msg):
        prints.append(schemas.PrinterIntentPrintText.model_validate_json(msg.data))

    async def cb_reply(msg):
        replies.append(schemas.AgentReply.model_validate_json(msg.data))

    await nc.subscribe(topics.PRINTER_INTENT_PRINT_TEXT, cb=cb_print)
    await nc.subscribe(topics.AGENT_REPLY, cb=cb_reply)

    class _IpQueryMic:
        def listen_once(self) -> str:
            return "hey lafufu what's your ip address"

    class _ExplodingOllama:
        async def chat(self, user_text: str) -> str:
            raise AssertionError("LLM must not be called for the IP intent")

    pipeline = VoicePipeline(
        nats_client=await nats.connect(nats_server, name="pipeline"),
        mic=_IpQueryMic(),
        ollama=_ExplodingOllama(),
        piper=FakePiper(chunks=[(b"\x00" * 1024, 0.5)]),
    )
    await pipeline.run_one_cycle()
    await asyncio.sleep(0.2)
    await nc.drain()

    assert len(prints) == 1, f"expected 1 print job, got {len(prints)}"
    assert "192.168.1.42" in prints[0].text
    assert "http://192.168.1.42:8080/admin" in prints[0].text
    assert len(replies) == 1, f"expected 1 reply, got {len(replies)}"
    assert replies[0].source == "system"
    assert "192.168.1.42" in replies[0].text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest "packages/agent/tests/test_pipeline.py::test_ip_intent_prints_and_speaks_without_llm" -v`
Expected: FAIL — `AssertionError: LLM must not be called for the IP intent` (the intercept does not exist yet, so the cycle falls through to `_ExplodingOllama.chat`).

- [ ] **Step 3: Update the pipeline imports**

In `packages/agent/src/lafufu_agent/pipeline.py`, replace the import block at the top of the file (currently lines 6-12):

```python
import asyncio
import logging
import threading
import time
from typing import Protocol

from lafufu_shared import nats_helper, schemas, topics
```

with:

```python
import asyncio
import logging
import socket
import threading
import time
from datetime import datetime
from typing import Protocol

from lafufu_shared import nats_helper, netinfo, schemas, topics

from .intents import build_ip_slip, match_ip_intent, spoken_ip_answer
```

- [ ] **Step 4: Add the intercept to `run_one_cycle`**

In `run_one_cycle`, find the transcript publish followed by the thinking section:

```python
        await nats_helper.publish_model(
            self.nats,
            topics.AGENT_TRANSCRIPT,
            schemas.AgentTranscript(text=clean, timestamp=time.time()),
        )

        # ---- Thinking ----
        await self._publish_state("thinking")
```

Replace it with (insert the intent block between the publish and the thinking section):

```python
        await nats_helper.publish_model(
            self.nats,
            topics.AGENT_TRANSCRIPT,
            schemas.AgentTranscript(text=clean, timestamp=time.time()),
        )

        # ---- System intents (answered directly, no LLM) ----
        if match_ip_intent(clean):
            await self._answer_ip_query()
            return

        # ---- Thinking ----
        await self._publish_state("thinking")
```

- [ ] **Step 5: Add the `_answer_ip_query` method**

In `packages/agent/src/lafufu_agent/pipeline.py`, add this method to the `VoicePipeline` class immediately after `run_one_cycle` (before `speak`):

```python
    async def _answer_ip_query(self) -> None:
        """Answer the 'what's your IP' voice intent directly: print a slip
        on the receipt printer and speak the address. Bypasses the LLM."""
        ip = netinfo.primary_lan_ip()
        if ip is not None:
            slip = build_ip_slip(ip, socket.gethostname(), datetime.now())
            await nats_helper.publish_model(
                self.nats,
                topics.PRINTER_INTENT_PRINT_TEXT,
                schemas.PrinterIntentPrintText(text=slip),
            )
        await self.speak(spoken_ip_answer(ip), emotion="neutral", source="system")
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest "packages/agent/tests/test_pipeline.py::test_ip_intent_prints_and_speaks_without_llm" -v`
Expected: PASS — 1 passed

- [ ] **Step 7: Run the full pipeline test file (no regressions)**

Run: `uv run pytest packages/agent/tests/test_pipeline.py -v`
Expected: PASS — all pipeline tests pass (the original ones still go through the LLM path because their mic returns `"hello lafufu"`, which does not match an IP intent).

- [ ] **Step 8: Lint**

Run: `uv run ruff check --fix packages/agent && uv run ruff format packages/agent`
Expected: no remaining errors.

- [ ] **Step 9: Commit**

```bash
git add packages/agent/src/lafufu_agent/pipeline.py packages/agent/tests/test_pipeline.py
git commit -m "feat(agent): answer \"what's your IP\" voice intent directly"
```

---

## Task 5: `lafufu-btcast` — Bluetooth IP broadcaster

A standalone root systemd service that advertises the IP in the Bluetooth adapter name while online and hides the adapter while offline. Not unit-testable; verified locally where possible and on the Pi.

**Files:**
- Create: `deploy/lafufu-btcast.sh`
- Create: `deploy/systemd/lafufu-btcast.service`

- [ ] **Step 1: Create the broadcaster script**

Create `deploy/lafufu-btcast.sh`:

```bash
#!/bin/bash
# lafufu-btcast — advertise the Pi's LAN IP as its Bluetooth adapter name.
#
# While the Pi is on a network the adapter is made discoverable and its
# alias is set to "lafufu <ip>", so the IP shows up in any phone's
# Bluetooth device list with no app and no pairing. While offline the
# adapter is made non-discoverable, so its absence means "no network".
#
# Runs as the lafufu-btcast systemd service (as root).
set -u

PYTHON="/srv/lafufu/.venv/bin/python"
POLL_INTERVAL=30
last_ip=""

bluetoothctl power on >/dev/null 2>&1 || true

while true; do
    ip="$("$PYTHON" -m lafufu_shared.netinfo 2>/dev/null || true)"

    if [[ -n "$ip" ]]; then
        bluetoothctl discoverable on >/dev/null 2>&1 || true
        if [[ "$ip" != "$last_ip" ]]; then
            bluetoothctl system-alias "lafufu $ip" >/dev/null 2>&1 || true
            echo "lafufu-btcast: online, alias set to 'lafufu $ip'"
            last_ip="$ip"
        fi
    else
        bluetoothctl discoverable off >/dev/null 2>&1 || true
        if [[ -n "$last_ip" ]]; then
            echo "lafufu-btcast: offline, adapter hidden"
            last_ip=""
        fi
    fi

    sleep "$POLL_INTERVAL"
done
```

- [ ] **Step 2: Create the systemd unit**

Create `deploy/systemd/lafufu-btcast.service`:

```ini
[Unit]
Description=Lafufu Bluetooth IP broadcast
After=bluetooth.service
Wants=bluetooth.service
PartOf=lafufu.target

[Service]
Type=simple
WorkingDirectory=/srv/lafufu
ExecStart=/bin/bash /srv/lafufu/deploy/lafufu-btcast.sh
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=lafufu.target
```

Note: no `User=` line — the unit runs as root, which `bluetoothctl` needs to
configure the adapter. `install.sh` step 9 already copies
`deploy/systemd/lafufu-*.service` via glob, so this unit ships automatically;
Task 6 only adds the `systemctl enable` entry.

- [ ] **Step 3: Verify the netinfo entry point the script depends on**

Run: `uv run python -m lafufu_shared.netinfo`
Expected: prints this machine's LAN IPv4 (e.g. `192.168.x.y`) on one line, or an
empty line if the dev machine is offline. This is the exact command the script
calls.

- [ ] **Step 4: Commit**

```bash
git add deploy/lafufu-btcast.sh deploy/systemd/lafufu-btcast.service
git commit -m "feat(deploy): lafufu-btcast Bluetooth IP broadcaster"
```

> **On-Pi verification (deferred to Task 7 — cannot run on the dev machine):**
> after deploy, `systemctl status lafufu-btcast` is active, `journalctl -u
> lafufu-btcast` shows an `online, alias set to 'lafufu <ip>'` line, and the Pi
> appears as `lafufu <ip>` in a phone's Bluetooth list.

---

## Task 6: `install.sh` — install `bluez`, enable the service, stay discoverable

**Files:**
- Modify: `deploy/install.sh`

- [ ] **Step 1: Add `bluez` to the system dependencies**

In `deploy/install.sh`, replace the apt-get install block (currently lines 20-23):

```bash
apt-get install -y python3.13 python3.13-venv python3-pip nodejs npm \
                   cups \
                   build-essential libasound2-dev portaudio19-dev \
                   curl ca-certificates git
```

with:

```bash
apt-get install -y python3.13 python3.13-venv python3-pip nodejs npm \
                   cups bluez \
                   build-essential libasound2-dev portaudio19-dev \
                   curl ca-certificates git
```

- [ ] **Step 2: Keep the Bluetooth adapter permanently discoverable**

In `deploy/install.sh`, find the systemd-units section (step 9):

```bash
# 9. systemd units
cp deploy/systemd/nats.service /etc/systemd/system/
cp deploy/systemd/lafufu-*.service /etc/systemd/system/
cp deploy/systemd/lafufu.target /etc/systemd/system/
systemctl daemon-reload
```

Insert this block immediately after it (before step 10):

```bash
# 9b. Bluetooth: keep the adapter permanently discoverable so the
#     lafufu-btcast IP broadcast is always visible (0 = no timeout).
if [[ -f /etc/bluetooth/main.conf ]]; then
  if grep -qE '^[[:space:]]*#?[[:space:]]*DiscoverableTimeout' /etc/bluetooth/main.conf; then
    sed -i -E 's/^[[:space:]]*#?[[:space:]]*DiscoverableTimeout.*/DiscoverableTimeout = 0/' \
      /etc/bluetooth/main.conf
  else
    sed -i '/^\[General\]/a DiscoverableTimeout = 0' /etc/bluetooth/main.conf
  fi
  systemctl restart bluetooth || true
fi
```

- [ ] **Step 3: Enable the `lafufu-btcast` service**

In `deploy/install.sh`, replace the enable block in step 10 (currently lines 92-94):

```bash
systemctl enable lafufu-animator.service lafufu-agent.service \
                 lafufu-printer.service lafufu-control.service \
                 lafufu-kiosk.service lafufu.target
```

with:

```bash
systemctl enable lafufu-animator.service lafufu-agent.service \
                 lafufu-printer.service lafufu-control.service \
                 lafufu-btcast.service lafufu-kiosk.service lafufu.target
```

- [ ] **Step 4: Syntax-check the script**

Run: `bash -n deploy/install.sh`
Expected: no output, exit code 0 (valid bash syntax).

- [ ] **Step 5: Commit**

```bash
git add deploy/install.sh
git commit -m "feat(deploy): install bluez + enable lafufu-btcast"
```

---

## Task 7: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS — **154 tests passing** (141 baseline + 2 netinfo + 2 schemas + 8 intents + 1 pipeline). Concretely: every test passes, 0 failures. If the count differs slightly, that is acceptable only if there are 0 failures and the new tests from Tasks 1–4 are all present and passing.

- [ ] **Step 2: Run the linter across the repo**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: no errors; formatting clean.

- [ ] **Step 3: On-Pi manual verification checklist**

These cannot run on the dev machine. After deploying to the Pi with
`sudo ./deploy/install.sh --update`:

1. `systemctl status lafufu-btcast` → `active (running)`.
2. `journalctl -u lafufu-btcast -n 20` → shows `online, alias set to 'lafufu <ip>'`.
3. On a phone, open the Bluetooth screen → `lafufu <ip>` appears in the device list (no pairing needed).
4. Disconnect the Pi from the network, wait ~30 s → the Pi disappears from the Bluetooth list; the journal logs `offline, adapter hidden`.
5. Reconnect → it reappears with the current IP.
6. Say to Lafufu: "what's your IP address" → a slip prints with the IP and `http://<ip>:8080/admin`, and Lafufu speaks the address. Confirm the admin chat shows the reply with no LLM "thinking" delay.

---

## Self-Review

**Spec coverage:**
- §4.1 `netinfo.py` (`primary_lan_ip` + `python -m` entry) → Task 1 ✓
- §4.2 Bluetooth broadcaster (`lafufu-btcast.sh`, systemd unit, `install.sh`, `bluez`, `DiscoverableTimeout=0`, discoverable-when-online / hidden-when-offline) → Tasks 5 & 6 ✓
- §4.3 voice intent (`intents.py`, `match_ip_intent`, pipeline intercept, print + speak, `source="system"`) → Tasks 2, 3, 4 ✓
- §4.4 printed slip (`build_ip_slip`) → Task 3 ✓
- §6 error handling: offline voice answer (`spoken_ip_answer(None)`) → Task 3; offline broadcaster (`discoverable off`) → Task 5; printer offline tolerated (fire-and-forget publish) → inherent in Task 4's NATS publish ✓
- §7 testing: `netinfo`, `match_ip_intent`, `build_ip_slip` unit tests, shell manual step → Tasks 1, 3, 5 ✓
- §9 files touched → matches the File Structure section above ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows complete content.

**Type consistency:** `primary_lan_ip() -> str | None`, `match_ip_intent(str) -> bool`, `build_ip_slip(ip, hostname, now)`, `spoken_ip_answer(str | None) -> str`, and `_answer_ip_query()` are referenced with identical signatures in Tasks 1, 3, 4. `AgentReply.source="system"` (Task 2) is used by `speak(..., source="system")` (Task 4). `PrinterIntentPrintText(text=...)` matches the existing schema (`text`, optional `title`).
