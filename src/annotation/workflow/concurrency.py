"""Bounded task execution with stable ordering and explicit batch barriers."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import RLock
from typing import Callable, Iterable, TypeVar
import os

T = TypeVar("T")
R = TypeVar("R")


def workflow_max_workers() -> int:
    value = int(os.getenv("WORKFLOW_MAX_WORKERS", "2"))
    if not 1 <= value <= 8:
        raise ValueError("WORKFLOW_MAX_WORKERS must be between 1 and 8")
    return value


def ordered_batches(function: Callable[[T], R], items: Iterable[T], *, workers: int,
                    stop: Callable[[R], bool] = lambda result: False) -> list[R]:
    """Finish each bounded batch before dispatch; never discard started work."""
    pending = list(items)
    results: list[R] = []
    if workers == 1:
        for item in pending:
            result = function(item)
            results.append(result)
            if stop(result):
                break
        return results
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="annotation-unit") as executor:
        for offset in range(0, len(pending), workers):
            futures = [executor.submit(copy_context().run, function, item)
                       for item in pending[offset:offset + workers]]
            batch = [future.result() for future in futures]
            results.extend(batch)
            if any(stop(result) for result in batch):
                break
    return results


class LockedSearch:
    """Serialize one shared client; do not wrap a whole retrieval router."""

    def __init__(self, tool):
        self.tool = tool
        self.lock = RLock()

    def __getattr__(self, name):
        return getattr(self.tool, name)

    def search(self, *args, **kwargs):
        with self.lock:
            return self.tool.search(*args, **kwargs)
