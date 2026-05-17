"""Shared pytest fixtures and fakes for cross-service testing."""

import subprocess
import time
from collections.abc import Callable, Iterator

import pytest


def nats_server_fixture(port: str = "4222") -> Callable:
    """Returns a pytest fixture that spawns a real nats-server on `port`."""

    @pytest.fixture(scope="module")
    def _fixture(tmp_path_factory) -> Iterator[str]:
        storedir = tmp_path_factory.mktemp(f"js_{port}")
        proc = subprocess.Popen(
            ["nats-server", "--port", port, "--jetstream", "--store_dir", str(storedir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        try:
            yield f"nats://localhost:{port}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    return _fixture


class FakeDxlBus:
    """In-memory fake of animator's DXL bus. Records writes, returns last positions."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, int]] = []
        self._positions: dict[str, int] = {}
        self._connected = True
        self.torque_disabled_count = 0

    def write(self, name: str, position: int) -> None:
        if not self._connected:
            raise OSError("DXL bus disconnected")
        self.writes.append((name, position))
        self._positions[name] = position

    def read(self, name: str) -> int:
        return self._positions.get(name, 0)

    def last_position(self, name: str) -> int | None:
        return self._positions.get(name)

    def expression_was_set(self, name: str) -> bool:
        """Convenience: true if any write happened that maps to the named expression."""
        # Implementations can extend this; default is no-op stub
        return False

    def disconnect(self) -> None:
        self._connected = False

    def reconnect(self) -> None:
        self._connected = True

    def disable_torque(self) -> None:
        self.torque_disabled_count += 1


class FakeWhisper:
    """Maps canned audio identifiers to canned transcripts."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    def transcribe(self, audio_id: str) -> str:
        self.calls.append(audio_id)
        return self.mapping.get(audio_id, "")


class FakeOllama:
    """Scripted replies keyed by prompt substring match."""

    def __init__(self, scripts: list[tuple[str, str]] | None = None) -> None:
        # list of (prompt_substring, reply_text) — first match wins
        self.scripts = scripts or []
        self.calls: list[str] = []

    async def chat(self, prompt: str) -> str:
        self.calls.append(prompt)
        for needle, reply in self.scripts:
            if needle.lower() in prompt.lower():
                return reply
        return "[neutral]\ndefault test reply"


class FakePiper:
    """Returns canned audio bytes + RMS sequence."""

    def __init__(self, chunks: list[tuple[bytes, float]] | None = None) -> None:
        # list of (audio_bytes, rms) tuples
        self.chunks = chunks or [(b"\x00" * 1764, 0.0)]
        self.calls: list[str] = []

    def synthesize(self, text: str) -> list[tuple[bytes, float]]:
        self.calls.append(text)
        return list(self.chunks)
