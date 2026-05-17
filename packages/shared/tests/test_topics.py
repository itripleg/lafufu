from lafufu_shared import topics


def test_state_subtopics_compose_correctly():
    assert topics.AGENT_STATE == "agent.state"
    assert topics.AGENT_STATE_IDLE == "agent.state.idle"
    assert topics.AGENT_STATE_WARMING == "agent.state.warming"
    assert topics.AGENT_STATE_LISTENING == "agent.state.listening"
    assert topics.AGENT_STATE_SPEAKING == "agent.state.speaking"


def test_animator_topics_present():
    assert topics.ANIMATOR_POSE == "animator.pose"
    assert topics.ANIMATOR_INTENT == "animator.intent"
    assert topics.ANIMATOR_INTENT_PREVIEW == "animator.intent.preview"
    assert topics.ANIMATOR_EVENT_GESTURE_DONE == "animator.event.gesture_done"


def test_printer_topics_present():
    assert topics.PRINTER_STATE_OFFLINE == "printer.state.offline"
    assert topics.PRINTER_INTENT_PRINT_TEXT == "printer.intent.print_text"


def test_system_topics_present():
    assert topics.SYSTEM_HEARTBEAT == "system.heartbeat"
    assert topics.SYSTEM_SERVICE_READY == "system.service.ready"
    assert topics.CONFIG_CHANGED == "config.changed"


def test_subscribe_wildcard_pattern():
    for s in (topics.AGENT_STATE_IDLE, topics.ANIMATOR_STATE_IDLE, topics.PRINTER_STATE_IDLE):
        assert ".state." in s
