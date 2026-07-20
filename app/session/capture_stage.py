"""Explicit Capture-stage state (Idle / Capturing + deferred requests).

Process-stage backlog lives in ``TranslationPipeline`` / ``pipeline_queue``.
This module only answers: is a grab in flight, and what should run next?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CapturePhase(StrEnum):
    IDLE = "idle"
    CAPTURING = "capturing"


@dataclass
class CaptureStage:
    """
    Single-flight capture coordinator.

    - At most one grab runs at a time (``phase is CAPTURING``).
    - While capturing: force requests set ``pending_force_recapture``;
      auto requests increment ``deferred_auto`` up to ``buffer_cap``.
    - After finish: ``pump()`` prefers force recapture over deferred autos.
    """

    phase: CapturePhase = CapturePhase.IDLE
    pending_force: bool = True
    deferred_auto: int = 0
    pending_force_recapture: bool = False
    overlay_was_visible: bool = True
    overlay_cloaked: bool = False
    overlay_saved_opacity: float = 1.0
    # Extra bookkeeping reserved for callers (not required for pure logic tests)
    _extra: dict = field(default_factory=dict, repr=False)

    @property
    def is_capturing(self) -> bool:
        return self.phase is CapturePhase.CAPTURING

    def reset_deferred(self) -> None:
        """Clear deferred auto / force-recapture (e.g. monitor start or force path)."""
        self.deferred_auto = 0
        self.pending_force_recapture = False

    def request(self, *, force: bool, buffer_cap: int) -> str:
        """
        Register a capture request.

        Returns:
            ``"start"`` — caller should begin a grab now (Idle → Capturing).
            ``"deferred"`` — already capturing; request recorded for later.
        """
        cap = max(0, int(buffer_cap))
        if self.phase is CapturePhase.CAPTURING:
            if force:
                self.pending_force_recapture = True
            else:
                room = max(0, cap - self.deferred_auto)
                if room > 0:
                    self.deferred_auto += 1
            return "deferred"

        if force:
            # Drop deferred autos so a manual path is not buried after this grab.
            self.deferred_auto = 0
            self.pending_force_recapture = False

        self.phase = CapturePhase.CAPTURING
        self.pending_force = force
        return "start"

    def begin_grab(
        self,
        *,
        force: bool,
        overlay_was_visible: bool,
        overlay_opacity: float,
    ) -> None:
        """Mark in-flight grab metadata (phase must already be CAPTURING or set here)."""
        self.phase = CapturePhase.CAPTURING
        self.pending_force = force
        self.overlay_was_visible = overlay_was_visible
        self.overlay_cloaked = False
        self.overlay_saved_opacity = float(overlay_opacity)

    def mark_cloaked(self) -> None:
        self.overlay_cloaked = True

    def finish(self) -> tuple[bool, bool]:
        """
        Capture thread finished (success or error). Idle phase; return presentation hints.

        Returns:
            (force_for_job, overlay_was_visible)
        """
        force = self.pending_force
        was_visible = self.overlay_was_visible
        self.phase = CapturePhase.IDLE
        return force, was_visible

    def take_cloak_restore(self) -> float | None:
        """If cloaked, clear flag and return opacity to restore; else None."""
        if not self.overlay_cloaked:
            return None
        self.overlay_cloaked = False
        return max(0.3, min(1.0, float(self.overlay_saved_opacity or 1.0)))

    def pump(self) -> str | None:
        """
        After finish, decide the next grab.

        Returns:
            ``"force"`` — start force capture (caller should clear Process auto queue).
            ``"auto"`` — start one deferred auto capture.
            ``None`` — nothing pending.
        """
        if self.phase is CapturePhase.CAPTURING:
            return None
        if self.pending_force_recapture:
            self.pending_force_recapture = False
            self.deferred_auto = 0
            self.phase = CapturePhase.CAPTURING
            self.pending_force = True
            return "force"
        if self.deferred_auto > 0:
            self.deferred_auto -= 1
            self.phase = CapturePhase.CAPTURING
            self.pending_force = False
            return "auto"
        return None
