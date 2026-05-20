# Lafufu Pi IP Discovery — Design

**Status:** Draft, awaiting review
**Date:** 2026-05-19
**Scope:** Make the Pi's LAN IP address discoverable two ways — an always-on Bluetooth broadcast and an on-demand voice trigger — so the admin UI can be reached without hunting for the IP.

---

## 1. Context

Lafufu's admin UI is served by the `control` service on `http://<pi-ip>:8080/admin`.
Reaching it requires knowing the Pi's LAN IP, which the user frequently cannot
find (DHCP reassignment, no console, router admin not handy). `install.sh`
prints the IP once at install time, but that value goes stale.

The Pi has a receipt printer (CUPS, driven by the `printer` service), a speaker,
and a microphone. The project currently has no Bluetooth or mDNS setup — this is
greenfield. Services communicate over a NATS bus; the `printer` service already
subscribes to `printer.intent.print_text` for arbitrary text printing.

## 2. Goals & Non-Goals

### Goals

- **Bluetooth broadcast** — the Pi advertises its IP in its Bluetooth adapter
  name (e.g. `lafufu 192.168.1.42`), visible in any phone's Bluetooth list with
  no app and no pairing. Always on, independent of the agent.
- **Voice trigger** — asking Lafufu out loud ("what's your IP address") makes it
  both print the IP on the receipt printer and speak it aloud.
- **Survives a downed agent** — the Bluetooth path must keep working even if the
  voice agent has crashed. This is the reason the two mechanisms both exist.

### Non-Goals

- mDNS / `lafufu.local` — may be explored later; out of scope here.
- A BLE GATT service or scanner-app-based readout — the IP-in-name approach is
  deliberately app-free.
- Surfacing any network info beyond the primary LAN IPv4 (hostname and admin URL
  are included on the printed slip only as convenience context).
- Wake-word detection — the voice trigger reuses the existing VAD-gated pipeline;
  the whole utterance is transcribed and phrase-matched.

## 3. Approach

The Bluetooth broadcaster runs as its **own standalone systemd service** with no
NATS dependency, rather than being folded into the `agent` or `control` service.
Folding it into the agent would tie a system-level concern to the voice pipeline
and — critically — kill the broadcast whenever the agent crashes, defeating the
"reachable when Lafufu is down" goal. The voice trigger, by contrast, is a small
intercept inside the existing agent pipeline because it inherently depends on the
voice loop already running.

## 4. Components

### 4.1 `lafufu_shared/netinfo.py` — shared IP helper

One pure, testable module used by both other components.

- `primary_lan_ip() -> str | None` — returns the primary LAN IPv4. Implementation
  uses the standard no-traffic trick: open a `SOCK_DGRAM` socket, `connect()` it
  toward `8.8.8.8:80` (no packets are actually sent), and read back
  `getsockname()[0]`. Returns `None` when the host is offline / has no route.
- `python -m lafufu_shared.netinfo` — prints the IP (empty line if `None`) so the
  shell broadcaster shares the exact same logic. Single source of truth.

### 4.2 Bluetooth broadcaster — new system service

- **`deploy/lafufu-btcast.sh`** — on start runs `bluetoothctl power on` and
  `bluetoothctl discoverable on`; then loops every ~30 s: resolve the IP via
  `python -m lafufu_shared.netinfo`, and when it has changed since the last
  iteration, set the adapter alias with
  `bluetoothctl system-alias "lafufu <ip>"` (or `"lafufu offline"` when no IP).
  `discoverable on` is re-asserted each iteration as cheap insurance.
- **`deploy/systemd/lafufu-btcast.service`** — runs as **root** (Bluetooth
  adapter configuration is a system task; running as root avoids polkit/group
  configuration). Added to `lafufu.target`. `Restart=on-failure` covers a hard
  crash; the internal loop covers transient `bluetoothctl` errors.
- **`install.sh` changes** — install the new unit and enable it via the target;
  ensure the `bluez` package is present; set `DiscoverableTimeout = 0` in
  `/etc/bluetooth/main.conf` so the adapter stays permanently discoverable.

Result: open any phone's Bluetooth screen and `lafufu 192.168.1.42` appears in
the device list.

### 4.3 Voice "what's your IP" intent — agent intercept

- **`lafufu_agent/intents.py`** (new) — `match_ip_intent(text: str) -> bool`.
  Lowercases the transcript and checks for anchor phrases: `"ip address"`,
  `"what's your ip"` / `"whats your ip"`, `"your ip"`, `"network address"`.
  Tolerant of Whisper's extra words (e.g. a leading "hey lafufu").
- **`pipeline.run_one_cycle` change** — after the transcript is published
  (`AGENT_TRANSCRIPT`) and **before** the `thinking` state / LLM call, test
  `match_ip_intent(clean)`. On a match the LLM is skipped entirely and the
  cycle handles the answer directly, then returns.
- **Answering** — resolve the IP via `netinfo.primary_lan_ip()`:
  - Speak via the existing `pipeline.speak(text, emotion="neutral",
    source="system")`. Spoken text: `"My IP address is 192.168.1.42. I have
    printed it for you."` This requires adding `"system"` to the
    `AgentReply.source` `Literal` in `schemas.py` (currently `"llm" | "puppet"`)
    so the admin UI can distinguish system-generated replies; the `speak()`
    signature's `source` parameter already accepts an arbitrary string.
  - Print by publishing `PRINTER_INTENT_PRINT_TEXT` with a
    `schemas.PrinterIntentPrintText` body containing the slip text (§4.4). The
    `printer` service already subscribes to this topic — same pattern existing
    code uses; no printer-side changes.

### 4.4 Printed slip

Plain text (most legible on a thermal printer):

```
   LAFUFU · NETWORK
hostname  lafufu
IP        192.168.1.42
admin     http://192.168.1.42:8080/admin
          2026-05-19 14:32
```

A pure `build_ip_slip(ip, hostname, now) -> str` builder, separate from the
intent matcher so it can be unit-tested independently.

## 5. Data Flow

**Bluetooth (continuous):**
`lafufu-btcast.sh` loop → `lafufu_shared.netinfo` → `bluetoothctl system-alias`
→ phone Bluetooth scan shows `lafufu <ip>`.

**Voice (on demand):**
mic utterance → STT → `run_one_cycle` publishes `AGENT_TRANSCRIPT` →
`match_ip_intent` true → `netinfo.primary_lan_ip()` → (a) `speak()` →
`AGENT_REPLY` + TTS audio; (b) publish `PRINTER_INTENT_PRINT_TEXT` →
`printer` service prints the slip. LLM is not called.

## 6. Error Handling

- **Offline (no IP):** voice intent speaks "I can't find a network connection
  right now" and prints nothing; broadcaster sets the alias to `lafufu offline`.
- **Printer offline:** the NATS publish is fire-and-forget — speech still
  happens, the cycle does not crash.
- **`bluetoothctl` missing or failing:** the broadcaster logs the error and
  continues looping; a hard crash is restarted by systemd.

## 7. Testing

TDD unit tests:

- `primary_lan_ip()` — mocked socket returns a known address; offline path
  returns `None`.
- `match_ip_intent()` — table of positive phrases (incl. a leading "hey lafufu")
  and negatives (ordinary chat that mentions unrelated words).
- `build_ip_slip()` — asserts IP, admin URL, and hostname appear in the output.

The shell broadcaster gets a documented manual verification step (run it, scan
from a phone, confirm the alias updates after an IP change).

## 8. Caveats

- A permanently discoverable adapter means anyone in Bluetooth range sees
  `lafufu <ip>`. Acceptable for a hobby device — a LAN IP is not sensitive.
- A phone that has already cached the device may show a stale name until it
  re-scans.
- Spoken IP digits can be hard to catch; the printed slip is the reliable
  channel and the spoken line is a courtesy.

## 9. Files Touched

**New:**
- `packages/shared/src/lafufu_shared/netinfo.py`
- `packages/agent/src/lafufu_agent/intents.py`
- `deploy/lafufu-btcast.sh`
- `deploy/systemd/lafufu-btcast.service`
- tests for `netinfo`, `intents`, and the slip builder

**Modified:**
- `packages/agent/src/lafufu_agent/pipeline.py` — intent intercept in
  `run_one_cycle`
- `packages/shared/src/lafufu_shared/schemas.py` — add `"system"` to the
  `AgentReply.source` `Literal`
- `deploy/install.sh` — install/enable the new unit, `bluez`, `main.conf` patch
