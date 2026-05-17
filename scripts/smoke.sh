#!/bin/bash
set -e

# On-Pi smoke test. Run after deploy.
# Exits non-zero on any failure.

echo "==> 1. All services active?"
for svc in nats.service lafufu-agent.service lafufu-animator.service lafufu-printer.service lafufu-control.service; do
  state=$(systemctl is-active "$svc" || true)
  if [[ "$state" != "active" ]]; then
    echo "FAIL: $svc is $state"
    exit 1
  fi
  echo "  ok: $svc"
done

echo "==> 2. control HTTP reachable?"
curl -sf http://localhost:8080/api/state/snapshot >/dev/null || { echo "FAIL: control HTTP"; exit 1; }
echo "  ok"

echo "==> 3. NATS reachable?"
nc -zv localhost 4222 || { echo "FAIL: NATS port"; exit 1; }

echo "==> 4. Trigger synthesized voice cycle (no real mic)"
curl -sf -X POST http://localhost:8080/api/agent/text_message -d '{"text":"smoke test"}' -H "Content-Type: application/json" >/dev/null || { echo "FAIL: text_message"; exit 1; }
sleep 8
echo "  ok (check logs for agent.reply)"

echo "==> 5. Trigger an expression directly"
curl -sf -X POST http://localhost:8080/api/animator/expression -d '{"name":"happy"}' -H "Content-Type: application/json" >/dev/null || { echo "FAIL: expression"; exit 1; }
sleep 1
echo "  ok"

echo "==> 6. Service status panel responds?"
curl -sf http://localhost:8080/api/state/snapshot | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "agent" in d.get("services",{}), d' || { echo "FAIL: snapshot missing agent heartbeat"; exit 1; }
echo "  ok"

echo "✅ smoke passed"
