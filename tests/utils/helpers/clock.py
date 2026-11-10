"""Deterministic clock for budget- and deadline-aware test seams."""

from __future__ import annotations


class FakeClock:
    """Clock whose wall and monotonic readings advance together unless a test splits them."""

    def __init__(self, *, wall: float = 1_000_000.0, monotonic: float = 50.0) -> None:
        self.wall = wall
        self.mono = monotonic
        self.sleeps: list[float] = []

    def time(self) -> float:
        """Return the fake wall-clock reading."""
        return self.wall

    def monotonic(self) -> float:
        """Return the fake monotonic reading."""
        return self.mono

    def sleep(self, seconds: float) -> None:
        """Record the sleep and advance both clocks."""
        self.sleeps.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        """Advance both clocks by *seconds*."""
        self.wall += seconds
        self.mono += seconds
