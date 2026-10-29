from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC).replace(microsecond=0)


class FrozenClock:
    """Test clock: time only moves when advance() is called, so expiry tests need no sleep."""

    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now += timedelta(seconds=seconds)
