"""Canonical NATS topic names. Services MUST reference these constants, not literals."""

# Agent state machine
AGENT_STATE = "agent.state"
AGENT_STATE_WARMING = f"{AGENT_STATE}.warming"
AGENT_STATE_IDLE = f"{AGENT_STATE}.idle"
AGENT_STATE_WAKE_LISTENING = f"{AGENT_STATE}.wake_listening"
AGENT_STATE_LISTENING = f"{AGENT_STATE}.listening"
AGENT_STATE_TRANSCRIBING = f"{AGENT_STATE}.transcribing"
AGENT_STATE_THINKING = f"{AGENT_STATE}.thinking"
AGENT_STATE_SPEAKING = f"{AGENT_STATE}.speaking"
AGENT_STATE_DEGRADED = f"{AGENT_STATE}.degraded"
AGENT_STATE_SHUTDOWN = f"{AGENT_STATE}.shutdown"

# Agent content
AGENT_TRANSCRIPT = "agent.transcript"
AGENT_REPLY = "agent.reply"
AGENT_TTS_RMS = "agent.tts.rms"
AGENT_INTENT = "agent.intent"
AGENT_INTENT_TEXT_MESSAGE = f"{AGENT_INTENT}.text_message"
AGENT_INTENT_SPEAK_TEXT = f"{AGENT_INTENT}.speak_text"

# Animator
ANIMATOR_STATE = "animator.state"
ANIMATOR_STATE_IDLE = f"{ANIMATOR_STATE}.idle"
ANIMATOR_STATE_ACTIVE = f"{ANIMATOR_STATE}.active"
ANIMATOR_STATE_DEGRADED = f"{ANIMATOR_STATE}.degraded"

ANIMATOR_POSE = "animator.pose"

ANIMATOR_INTENT = "animator.intent"
ANIMATOR_INTENT_SET_POSE = f"{ANIMATOR_INTENT}.set_pose"
ANIMATOR_INTENT_PREVIEW = f"{ANIMATOR_INTENT}.preview"
ANIMATOR_INTENT_PLAY_EXPRESSION = f"{ANIMATOR_INTENT}.play_expression"
ANIMATOR_INTENT_GESTURE = f"{ANIMATOR_INTENT}.gesture"

ANIMATOR_EVENT = "animator.event"
ANIMATOR_EVENT_GESTURE_DONE = f"{ANIMATOR_EVENT}.gesture_done"
ANIMATOR_EVENT_LIPSYNC_START = f"{ANIMATOR_EVENT}.lipsync_start"
ANIMATOR_EVENT_LIPSYNC_END = f"{ANIMATOR_EVENT}.lipsync_end"

# Request-reply: animator asks control to (re)publish the idle expression
# after its own subscriptions are active. Closes the cold-boot race where
# control's startup-time _publish_idle_expression fires before the animator
# has subscribed and the message is silently dropped.
# See docs/superpowers/specs/2026-05-26-idle-bootstrap-readiness-design.md.
ANIMATOR_REQUEST_IDLE = "animator.request.idle"

# Printer
PRINTER_STATE = "printer.state"
PRINTER_STATE_IDLE = f"{PRINTER_STATE}.idle"
PRINTER_STATE_PRINTING = f"{PRINTER_STATE}.printing"
PRINTER_STATE_ERROR = f"{PRINTER_STATE}.error"
PRINTER_STATE_OFFLINE = f"{PRINTER_STATE}.offline"

PRINTER_INTENT = "printer.intent"
PRINTER_INTENT_PRINT_TEXT = f"{PRINTER_INTENT}.print_text"
PRINTER_INTENT_PRINT_TRANSCRIPT = f"{PRINTER_INTENT}.print_transcript"
PRINTER_INTENT_TEST_PAGE = f"{PRINTER_INTENT}.test_page"
PRINTER_INTENT_PRINT_FILE = f"{PRINTER_INTENT}.print_file"
PRINTER_INTENT_COMPOSE = f"{PRINTER_INTENT}.compose"

PRINTER_EVENT = "printer.event"
PRINTER_EVENT_JOB_STARTED = f"{PRINTER_EVENT}.job_started"
PRINTER_EVENT_JOB_DONE = f"{PRINTER_EVENT}.job_done"
PRINTER_EVENT_PAPER_OUT = f"{PRINTER_EVENT}.paper_out"
PRINTER_EVENT_JAM = f"{PRINTER_EVENT}.jam"

# Config + system
CONFIG_CHANGED = "config.changed"  # actual topic: f"{CONFIG_CHANGED}.{dotted_key}"
# Services publish this on startup so control re-broadcasts every setting via
# config.changed.<key>. Lets services sync to the DB (source of truth) on every
# restart instead of drifting away from it using env-var defaults.
CONFIG_SNAPSHOT_REQUEST = "config.snapshot.request"

SYSTEM_HEARTBEAT = "system.heartbeat"  # f"{...}.<service>"
SYSTEM_SERVICE = "system.service"
SYSTEM_SERVICE_STARTING = f"{SYSTEM_SERVICE}.starting"
SYSTEM_SERVICE_READY = f"{SYSTEM_SERVICE}.ready"
SYSTEM_SERVICE_RESTARTING = f"{SYSTEM_SERVICE}.restarting"
SYSTEM_SERVICE_STOPPED = f"{SYSTEM_SERVICE}.stopped"

SYSTEM_ERROR = "system.error"  # f"{...}.<service>.<kind>"
