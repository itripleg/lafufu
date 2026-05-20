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
