"""Bounded FIFO helpers for the Process-stage job buffer."""

from __future__ import annotations

from collections import deque
from typing import Any, TypeVar

from app.config import (
    PIPELINE_BUFFER_DEFAULT,
    clamp_pipeline_buffer_size,
)

T = TypeVar("T")


def buffer_cap(value: Any) -> int:
    """Normalize a buffer size from config or UI."""
    if value is None:
        return PIPELINE_BUFFER_DEFAULT
    return clamp_pipeline_buffer_size(value)


def enqueue_job(
    queue: deque[T],
    job: T,
    *,
    cap: int,
    force: bool = False,
    is_force: Any | None = None,
) -> deque[T]:
    """
    Push *job* into a bounded waiting queue (does not include the running job).

    - force=True: drop all waiting auto jobs, keep at most one force at front.
    - force=False: append; if over *cap*, drop oldest until len <= cap.
    - is_force(job) -> bool: optional predicate; default uses job.force attribute.
    """
    cap_n = buffer_cap(cap)
    if is_force is None:

        def is_force(j: T) -> bool:  # type: ignore[misc]
            return bool(getattr(j, "force", False))

    if force:
        # Drop waiting auto jobs; force becomes the only (or new) waiter at front.
        queue.clear()
        queue.append(job)
        return queue

    queue.append(job)
    while len(queue) > cap_n:
        # Prefer dropping oldest non-force so a waiting manual job is not evicted.
        if len(queue) >= 2 and is_force(queue[0]):
            force_j = queue.popleft()
            queue.popleft()
            queue.appendleft(force_j)
        else:
            queue.popleft()
    return queue


def make_queue(maxlen_hint: int | None = None) -> deque[Any]:
    """Create an unbounded deque; capacity is enforced by enqueue_job."""
    _ = maxlen_hint  # capacity applied at enqueue time (config can change)
    return deque()
