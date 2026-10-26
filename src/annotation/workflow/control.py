"""Cooperative controls for a workflow execution."""

from __future__ import annotations

import threading


class RunStopped(Exception):
    """Raised at a workflow boundary after a user requested cancellation."""


class RunControl:
    """Thread-safe stop request shared by the API and workflow worker."""

    def __init__(self) -> None:
        self._stop = threading.Event()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> bool:
        """Request stop and return whether this call changed the state."""
        was_set = self._stop.is_set()
        self._stop.set()
        return not was_set

    def raise_if_stopped(self) -> None:
        if self.stop_requested:
            raise RunStopped("user_requested_stop")
