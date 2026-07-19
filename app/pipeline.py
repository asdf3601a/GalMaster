"""Process stage: OCR/VLM → translate on a worker thread.

Capture is owned by AppController / CaptureStage; jobs must include a pre-grabbed image.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha1
import io
import time
from typing import Callable, TypeVar

from PIL import Image
from PySide6.QtCore import QObject, Qt, QThread, Signal

from app.capture.screenshot import (
    describe_image,
    is_mostly_blank,
    make_preview_image,
)
from app.config import AppConfig, LlmEndpointConfig
from app.i18n import tr
from app.ocr.base import OCREngine, create_ocr_engine
from app.pipeline_queue import buffer_cap, enqueue_job
from app.translate.cache import TranslationCache
from app.translate.llm_translator import (
    LLMTranslator,
    sampling_fingerprint,
)

T = TypeVar("T")

# Circuit breaker: separate counters per endpoint role
_CB_FAIL_THRESHOLD = 3
_CB_COOLDOWN_S = 30.0


@dataclass
class PipelineResult:
    source_text: str
    translated_text: str
    from_cache: bool = False
    error: str = ""
    ocr_only: bool = False  # True when LLM skipped (no API key)
    # True when OCR text matches previous (auto-monitor dedupe). Status only — not overlay content.
    skipped: bool = False
    status_message: str = ""


@dataclass
class PipelineJob:
    cfg: AppConfig
    image: Image.Image  # required — Capture stage supplies the frame
    force: bool = False  # manual/hotkey: always run even if text unchanged


class _CircuitBreaker:
    """Per-endpoint consecutive-failure cooldown (auto-monitor friendly)."""

    def __init__(self) -> None:
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def allow(self, role: str, *, force: bool) -> bool:
        if force:
            return True
        until = self._open_until.get(role, 0.0)
        return time.monotonic() >= until

    def record_success(self, role: str) -> None:
        self._fails[role] = 0
        self._open_until.pop(role, None)

    def record_failure(self, role: str) -> None:
        n = self._fails.get(role, 0) + 1
        self._fails[role] = n
        if n >= _CB_FAIL_THRESHOLD:
            self._open_until[role] = time.monotonic() + _CB_COOLDOWN_S

    def cooldown_remaining(self, role: str) -> float:
        until = self._open_until.get(role, 0.0)
        return max(0.0, until - time.monotonic())


class _Worker(QObject):
    finished = Signal(object)  # PipelineResult
    progress = Signal(str)
    preview_ready = Signal(object)  # small PIL Image for UI

    def __init__(self) -> None:
        super().__init__()
        self._ocr: OCREngine | None = None
        self._ocr_kind = ""
        self._cache = TranslationCache()
        self._last_source = ""
        self._last_image_fp = ""
        # Sliding window of (source, translation) for LLM context
        self._history: deque[tuple[str, str]] = deque(maxlen=32)
        self._abort = False
        self._generation = 0
        self._breaker = _CircuitBreaker()
        # One pool for deadline-wrapped LLM calls (orphan threads die with process)
        self._llm_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-call")

    def request_abort(self) -> None:
        self._abort = True
        self._generation += 1

    def clear_abort(self) -> None:
        self._abort = False

    def run_job(self, job: object) -> None:
        if isinstance(job, PipelineJob):
            cfg = job.cfg
            img = job.image
            force = job.force
        else:
            self.finished.emit(
                PipelineResult("", "", error=tr("pipe.invalid_job"))
            )
            return

        # Honour abort set before this job started (e.g. shutdown).
        # After a successful pass through this check, clear sticky abort so the
        # next intentional job is not skipped after a prior cancel.
        if self._abort:
            self.finished.emit(
                PipelineResult(
                    "", "", skipped=True, status_message=tr("pipe.cancelled")
                )
            )
            return
        self.clear_abort()
        gen = self._generation

        try:
            if img is None:
                # Capture is a separate stage; Process never grabs the screen.
                self.finished.emit(
                    PipelineResult("", "", error=tr("pipe.no_image"))
                )
                return

            if self._abort or gen != self._generation:
                self.finished.emit(
                    PipelineResult(
                        "", "", skipped=True, status_message=tr("pipe.cancelled")
                    )
                )
                return

            self.progress.emit(tr("pipe.prep_image", info=describe_image(img)))
            try:
                self.preview_ready.emit(make_preview_image(img))
            except Exception:
                pass

            if is_mostly_blank(img):
                # Status only — keep last overlay / result panel content
                self.finished.emit(
                    PipelineResult(
                        "",
                        "",
                        skipped=True,
                        status_message=tr(
                            "pipe.blank", info=describe_image(img)
                        ),
                    )
                )
                return

            mode = (getattr(cfg, "pipeline_mode", "ocr") or "ocr").strip().lower()
            if mode == "vlm":
                self._run_vlm(cfg, img, force=force, gen=gen)
            elif mode == "vlm_ocr":
                self._run_vlm_ocr(cfg, img, force=force, gen=gen)
            else:
                self._run_ocr(cfg, img, force=force, gen=gen)
        except Exception as exc:
            self.finished.emit(
                PipelineResult("", "", error=tr("pipe.error", err=str(exc)))
            )

    def _run_with_deadline(
        self,
        fn: Callable[[], T],
        *,
        timeout_s: float,
        gen: int,
    ) -> T:
        """Run fn in a pool thread; raise RuntimeError on wall-clock timeout or abort."""
        # Margin so HTTP timeout can fire first when it works
        wall = max(6.0, float(timeout_s) + 5.0)
        future = self._llm_pool.submit(fn)
        try:
            result = future.result(timeout=wall)
        except FuturesTimeout as exc:
            future.cancel()
            raise RuntimeError(f"連線逾時（{timeout_s:.0f}s）") from exc
        if self._abort or gen != self._generation:
            raise RuntimeError(tr("pipe.cancelled"))
        return result

    def _call_llm(
        self,
        role: str,
        fn: Callable[[], T],
        *,
        timeout_s: float,
        force: bool,
        gen: int,
    ) -> T:
        if not self._breaker.allow(role, force=force):
            rem = int(self._breaker.cooldown_remaining(role))
            raise RuntimeError(tr("pipe.llm_cooldown", n=rem or 1))
        try:
            out = self._run_with_deadline(fn, timeout_s=timeout_s, gen=gen)
            self._breaker.record_success(role)
            return out
        except Exception:
            # Do not open the breaker for user cancel / generation invalidate
            if not (self._abort or gen != self._generation):
                self._breaker.record_failure(role)
            raise

    def _run_ocr(
        self, cfg: AppConfig, img: Image.Image, *, force: bool, gen: int
    ) -> None:
        ocr = self._get_ocr(cfg.ocr_engine, getattr(cfg, "source_lang", "ja") or "ja")
        backend = getattr(ocr, "backend_label", None) or cfg.ocr_engine
        self.progress.emit(tr("pipe.ocr_running", backend=backend))
        source = ocr.recognize(img).strip()
        if self._abort or gen != self._generation:
            self.finished.emit(
                PipelineResult(
                    "", "", skipped=True, status_message=tr("pipe.cancelled")
                )
            )
            return
        if not source:
            # No text: refresh status only — do not wipe overlay or last result
            self.finished.emit(
                PipelineResult(
                    "",
                    "",
                    skipped=True,
                    status_message=tr(
                        "pipe.ocr_empty", info=describe_image(img)
                    ),
                )
            )
            return

        # Auto-monitor: skip unchanged text. Manual/hotkey (force=True): always continue.
        if not force and source == self._last_source:
            self.finished.emit(
                PipelineResult(
                    source,
                    "",
                    skipped=True,
                    status_message=tr("pipe.unchanged"),
                )
            )
            return
        self._last_source = source

        hist_n = max(0, int(getattr(cfg, "context_history_size", 3) or 0))
        history = list(self._history)[-hist_n:] if hist_n > 0 else []
        tr_ep = cfg.translate_endpoint()

        if not tr_ep.has_key:
            self.progress.emit(tr("pipe.ocr_done_no_llm"))
            self._push_history(source, "")
            self.finished.emit(PipelineResult(source, "", ocr_only=True))
            return

        samp = self._endpoint_sampling_fp(tr_ep)
        cache_key = self._make_cache_key(
            f"ocr|{source}",
            cfg.source_lang,
            cfg.target_lang,
            tr_ep.model,
            history,
            samp,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.progress.emit(tr("pipe.cache"))
            self._push_history(source, cached)
            self.finished.emit(PipelineResult(source, cached, from_cache=True))
            return

        if self._abort or gen != self._generation:
            self.finished.emit(
                PipelineResult(
                    "", "", skipped=True, status_message=tr("pipe.cancelled")
                )
            )
            return

        self.progress.emit(
            tr("pipe.llm_ctx", n=len(history), total=hist_n)
            if hist_n
            else tr("pipe.llm")
        )
        try:
            translator = self._make_translator(tr_ep)
            translated = self._call_llm(
                "translate",
                lambda: translator.translate(
                    source,
                    cfg.source_lang,
                    cfg.target_lang,
                    history=history,
                ),
                timeout_s=tr_ep.timeout_s,
                force=force,
                gen=gen,
            )
        except Exception as exc:
            # Soft error: keep OCR text so UI/OBS stay usable; never hang on API faults.
            self.finished.emit(
                PipelineResult(
                    source,
                    "",
                    error=tr("pipe.llm_error", err=str(exc)[:500]),
                )
            )
            return
        self._cache.put(cache_key, translated)
        self._push_history(source, translated)
        self.progress.emit(tr("pipe.translate_done"))
        self.finished.emit(PipelineResult(source, translated, from_cache=False))

    def _run_vlm(
        self, cfg: AppConfig, img: Image.Image, *, force: bool, gen: int
    ) -> None:
        vlm_ep = cfg.vlm_endpoint()
        if not vlm_ep.has_key:
            self.finished.emit(
                PipelineResult("", "", error=tr("pipe.vlm_need_key"))
            )
            return

        img_fp = self._image_fingerprint(img)
        if not force and img_fp and img_fp == self._last_image_fp:
            self.finished.emit(
                PipelineResult(
                    "",
                    "",
                    skipped=True,
                    status_message=tr("pipe.vlm_unchanged"),
                )
            )
            return

        hist_n = max(0, int(getattr(cfg, "context_history_size", 3) or 0))
        history = list(self._history)[-hist_n:] if hist_n > 0 else []
        samp = self._endpoint_sampling_fp(vlm_ep)
        cache_key = self._make_cache_key(
            f"vlm|{img_fp}",
            cfg.source_lang,
            cfg.target_lang,
            vlm_ep.model,
            history,
            samp,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            # Cache stores "source\x1etranslation"
            if "\x1e" in cached:
                src_c, tr_c = cached.split("\x1e", 1)
            else:
                src_c, tr_c = "", cached
            self.progress.emit(tr("pipe.cache"))
            self._last_image_fp = img_fp
            self._push_history(src_c or "(VLM)", tr_c)
            self.finished.emit(PipelineResult(src_c, tr_c, from_cache=True))
            return

        if self._abort or gen != self._generation:
            self.finished.emit(
                PipelineResult(
                    "", "", skipped=True, status_message=tr("pipe.cancelled")
                )
            )
            return

        self.progress.emit(
            tr("pipe.vlm_ctx", n=len(history), total=hist_n)
            if hist_n
            else tr("pipe.vlm")
        )
        try:
            translator = self._make_translator(vlm_ep)
            source, translated = self._call_llm(
                "vlm",
                lambda: translator.translate_image(
                    img,
                    cfg.source_lang,
                    cfg.target_lang,
                    history=history,
                ),
                timeout_s=vlm_ep.timeout_s,
                force=force,
                gen=gen,
            )
        except Exception as exc:
            # Mark frame seen so auto-monitor does not immediately re-spam the same image.
            self._last_image_fp = img_fp
            self.finished.emit(
                PipelineResult(
                    "",
                    "",
                    error=tr("pipe.llm_error", err=str(exc)[:500]),
                )
            )
            return
        if not (translated or "").strip() and not (source or "").strip():
            self._last_image_fp = img_fp
            self.finished.emit(
                PipelineResult("", "", error=tr("pipe.vlm_empty"))
            )
            return
        if not (translated or "").strip() and (source or "").strip():
            translated = source  # model only returned source
        source = (source or "").strip() or "(VLM)"
        translated = (translated or "").strip()
        self._cache.put(cache_key, f"{source}\x1e{translated}")
        self._last_image_fp = img_fp
        self._push_history(source, translated)
        self.progress.emit(tr("pipe.translate_done"))
        self.finished.emit(PipelineResult(source, translated, from_cache=False))

    def _run_vlm_ocr(
        self, cfg: AppConfig, img: Image.Image, *, force: bool, gen: int
    ) -> None:
        """VLM recognize (vision endpoint) → optional text translate (translate endpoint)."""
        vlm_ep = cfg.vlm_endpoint()
        tr_ep = cfg.translate_endpoint()
        if not vlm_ep.has_key:
            self.finished.emit(
                PipelineResult("", "", error=tr("pipe.vlm_ocr_need_key"))
            )
            return

        img_fp = self._image_fingerprint(img)
        if not force and img_fp and img_fp == self._last_image_fp:
            self.finished.emit(
                PipelineResult(
                    "",
                    "",
                    skipped=True,
                    status_message=tr("pipe.vlm_unchanged"),
                )
            )
            return

        if self._abort or gen != self._generation:
            self.finished.emit(
                PipelineResult(
                    "", "", skipped=True, status_message=tr("pipe.cancelled")
                )
            )
            return

        self.progress.emit(tr("pipe.vlm_ocr"))
        try:
            vlm = self._make_translator(vlm_ep)
            source = self._call_llm(
                "vlm",
                lambda: vlm.recognize_image(
                    img, getattr(cfg, "source_lang", "ja") or "ja"
                ),
                timeout_s=vlm_ep.timeout_s,
                force=force,
                gen=gen,
            )
            source = (source or "").strip()
        except Exception as exc:
            self._last_image_fp = img_fp
            self.finished.emit(
                PipelineResult(
                    "",
                    "",
                    error=tr("pipe.llm_error", err=str(exc)[:500]),
                )
            )
            return

        if self._abort or gen != self._generation:
            self.finished.emit(
                PipelineResult(
                    "", "", skipped=True, status_message=tr("pipe.cancelled")
                )
            )
            return

        if not source:
            self._last_image_fp = img_fp
            self.finished.emit(
                PipelineResult(
                    "",
                    "",
                    skipped=True,
                    status_message=tr(
                        "pipe.ocr_empty", info=describe_image(img)
                    ),
                )
            )
            return

        if not force and source == self._last_source:
            self._last_image_fp = img_fp
            self.finished.emit(
                PipelineResult(
                    source,
                    "",
                    skipped=True,
                    status_message=tr("pipe.unchanged"),
                )
            )
            return
        self._last_source = source
        self._last_image_fp = img_fp

        hist_n = max(0, int(getattr(cfg, "context_history_size", 3) or 0))
        history = list(self._history)[-hist_n:] if hist_n > 0 else []

        if not tr_ep.has_key:
            self.progress.emit(tr("pipe.ocr_done_no_llm"))
            self._push_history(source, "")
            self.finished.emit(PipelineResult(source, "", ocr_only=True))
            return

        samp = self._endpoint_sampling_fp(tr_ep)
        cache_key = self._make_cache_key(
            f"vlm_ocr|{source}",
            cfg.source_lang,
            cfg.target_lang,
            tr_ep.model,
            history,
            samp,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.progress.emit(tr("pipe.cache"))
            self._push_history(source, cached)
            self.finished.emit(PipelineResult(source, cached, from_cache=True))
            return

        self.progress.emit(
            tr("pipe.llm_ctx", n=len(history), total=hist_n)
            if hist_n
            else tr("pipe.llm")
        )
        try:
            translator = self._make_translator(tr_ep)
            translated = self._call_llm(
                "translate",
                lambda: translator.translate(
                    source,
                    cfg.source_lang,
                    cfg.target_lang,
                    history=history,
                ),
                timeout_s=tr_ep.timeout_s,
                force=force,
                gen=gen,
            )
        except Exception as exc:
            self.finished.emit(
                PipelineResult(
                    source,
                    "",
                    error=tr("pipe.llm_error", err=str(exc)[:500]),
                )
            )
            return
        self._cache.put(cache_key, translated)
        self._push_history(source, translated)
        self.progress.emit(tr("pipe.translate_done"))
        self.finished.emit(PipelineResult(source, translated, from_cache=False))

    @staticmethod
    def _make_translator(ep: LlmEndpointConfig) -> LLMTranslator:
        return LLMTranslator.from_endpoint(ep)

    @staticmethod
    def _endpoint_sampling_fp(ep: LlmEndpointConfig) -> str:
        return sampling_fingerprint(
            temperature=ep.temperature,
            top_p=ep.top_p,
            top_k=ep.top_k,
            frequency_penalty=ep.frequency_penalty,
            presence_penalty=ep.presence_penalty,
            reasoning_effort=ep.reasoning_effort or "",
            seed=ep.seed,
            max_tokens=int(ep.max_tokens or 2048),
        )

    @staticmethod
    def _image_fingerprint(img: Image.Image) -> str:
        """Cheap content hash for VLM dedupe (downscaled grayscale)."""
        try:
            small = img.convert("L")
            small.thumbnail((64, 64))
            buf = io.BytesIO()
            small.save(buf, format="PNG")
            return sha1(buf.getvalue()).hexdigest()
        except Exception:
            return sha1(img.tobytes()).hexdigest()

    @staticmethod
    def _make_cache_key(
        source: str,
        source_lang: str,
        target_lang: str,
        model: str,
        history: list[tuple[str, str]],
        sampling_fp: str = "",
    ) -> str:
        base = TranslationCache.make_key(source, source_lang, target_lang, model)
        if sampling_fp:
            base = f"{base}|s:{sha1(sampling_fp.encode()).hexdigest()[:12]}"
        if not history:
            return base
        blob = "\n".join(f"{s}\t{t}" for s, t in history)
        digest = sha1(blob.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"{base}|h:{digest}"

    def _push_history(self, source: str, translation: str) -> None:
        source = (source or "").strip()
        if not source:
            return
        self._history.append((source, (translation or "").strip()))

    def _get_ocr(self, kind: str, lang: str = "ja") -> OCREngine:
        key = f"{kind}:{lang}"
        if self._ocr is None or self._ocr_kind != key:
            self.progress.emit(tr("pipe.ocr_load", kind=kind))
            self._ocr = create_ocr_engine(kind, lang=lang)
            self._ocr_kind = key
            label = getattr(self._ocr, "backend_label", None)
            if label:
                self.progress.emit(tr("pipe.ocr_ready", label=label))
        return self._ocr

    def reset_dedupe(self) -> None:
        self._last_source = ""
        self._last_image_fp = ""

    def clear_history(self) -> None:
        self._history.clear()

    def clear_cache(self) -> None:
        self._cache.clear()


class TranslationPipeline(QObject):
    """Serializes pipeline jobs on a background QThread."""

    finished = Signal(object)
    progress = Signal(str)
    preview_ready = Signal(object)  # PIL Image (thumbnail)
    busy_changed = Signal(bool)
    _submit = Signal(object)  # PipelineJob
    _cmd_reset_dedupe = Signal()
    _cmd_clear_history = Signal()
    _cmd_clear_cache = Signal()
    _cmd_abort = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker = _Worker()
        self._worker.moveToThread(self._thread)
        queued = Qt.ConnectionType.QueuedConnection
        self._submit.connect(self._worker.run_job, queued)
        self._cmd_reset_dedupe.connect(self._worker.reset_dedupe, queued)
        self._cmd_clear_history.connect(self._worker.clear_history, queued)
        self._cmd_clear_cache.connect(self._worker.clear_cache, queued)
        self._cmd_clear_cache.connect(self._worker.clear_history, queued)
        self._cmd_abort.connect(self._worker.request_abort, queued)
        self._worker.finished.connect(self._on_finished)
        self._worker.progress.connect(self.progress)
        self._worker.preview_ready.connect(self.preview_ready)
        self._thread.start()
        self._busy = False
        # Waiting jobs only (running job is not stored here). Bounded by cfg.
        self._queue: deque[PipelineJob] = deque()

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def queue_depth(self) -> int:
        """Number of jobs waiting (not including the running job)."""
        return len(self._queue)

    def clear_auto_queue(self) -> None:
        """Drop waiting auto (non-force) jobs; keep force jobs if any."""
        if not self._queue:
            return
        kept = [j for j in self._queue if j.force]
        self._queue.clear()
        self._queue.extend(kept)

    def request(
        self,
        cfg: AppConfig,
        image: Image.Image,
        *,
        force: bool = False,
    ) -> None:
        # Snapshot config so mid-job UI mutations cannot race the worker
        job = PipelineJob(cfg=deepcopy(cfg), image=image, force=force)
        cap = buffer_cap(getattr(cfg, "pipeline_buffer_size", 3))
        if self._busy:
            # If a force job is already waiting, preserve force on coalesce path
            if force:
                enqueue_job(self._queue, job, cap=cap, force=True)
            else:
                enqueue_job(self._queue, job, cap=cap, force=False)
            return
        self._busy = True
        self.busy_changed.emit(True)
        self._submit.emit(job)

    def _on_finished(self, result: object) -> None:
        # Always clear busy / drain queue even if a UI slot raises on the result.
        try:
            self.finished.emit(result)
        finally:
            if self._queue:
                job = self._queue.popleft()
                # Stay busy for the next queued job
                self._submit.emit(job)
            else:
                self._busy = False
                self.busy_changed.emit(False)

    def reset_dedupe(self) -> None:
        self._cmd_reset_dedupe.emit()

    def clear_history(self) -> None:
        self._cmd_clear_history.emit()

    def clear_cache(self) -> None:
        self._cmd_clear_cache.emit()

    def shutdown(self) -> None:
        self._queue.clear()
        self._cmd_abort.emit()
        # Do not block the UI thread for long — abandon residual LLM work.
        self._thread.quit()
        if not self._thread.wait(1000):
            pass
        try:
            self._worker._llm_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
