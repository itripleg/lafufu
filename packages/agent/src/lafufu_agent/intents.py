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
