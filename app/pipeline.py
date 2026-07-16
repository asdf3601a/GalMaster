"""Capture → OCR/VLM → translate pipeline running on a worker thread."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha1
import io

from PIL import Image
from PySide6.QtCore import QObject, Qt, QThread, Signal

from app.capture.screenshot import (
    capture_region,
    describe_image,
    is_mostly_blank,
    make_preview_image,
)
from app.config import AppConfig
from app.i18n import tr
from app.ocr.base import OCREngine, create_ocr_engine
from app.translate.cache import TranslationCache
from app.translate.llm_translator import (
    LLMTranslator,
    sampling_fingerprint,
)


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
    image: Image.Image | None = None
    force: bool = False  # manual/hotkey: always run even if text unchanged


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

    def request_abort(self) -> None:
        self._abort = True

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

        try:
            if img is None:
                if not cfg.has_region and not getattr(cfg, "has_abs_region", False):
                    self.finished.emit(
                        PipelineResult("", "", error=tr("pipe.no_region"))
                    )
                    return
                self.progress.emit(tr("pipe.capturing"))
                img = capture_region(
                    hwnd=cfg.bound_hwnd or None,
                    rel_x=cfg.region_x,
                    rel_y=cfg.region_y,
                    rel_w=cfg.region_w,
                    rel_h=cfg.region_h,
                    abs_x=int(getattr(cfg, "region_abs_x", 0) or 0),
                    abs_y=int(getattr(cfg, "region_abs_y", 0) or 0),
                    abs_w=int(getattr(cfg, "region_abs_w", 0) or 0),
                    abs_h=int(getattr(cfg, "region_abs_h", 0) or 0),
                )

            if self._abort:
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
                self.finished.emit(
                    PipelineResult(
                        "",
                        "",
                        error=tr("pipe.blank", info=describe_image(img)),
                    )
                )
                return

            mode = (getattr(cfg, "pipeline_mode", "ocr") or "ocr").strip().lower()
            if mode == "vlm":
                self._run_vlm(cfg, img, force=force)
            else:
                self._run_ocr(cfg, img, force=force)
        except Exception as exc:
            self.finished.emit(
                PipelineResult("", "", error=tr("pipe.error", err=str(exc)))
            )

    def _run_ocr(self, cfg: AppConfig, img: Image.Image, *, force: bool) -> None:
        ocr = self._get_ocr(cfg.ocr_engine, getattr(cfg, "source_lang", "ja") or "ja")
        backend = getattr(ocr, "backend_label", None) or cfg.ocr_engine
        self.progress.emit(tr("pipe.ocr_running", backend=backend))
        source = ocr.recognize(img).strip()
        if self._abort:
            self.finished.emit(
                PipelineResult(
                    "", "", skipped=True, status_message=tr("pipe.cancelled")
                )
            )
            return
        if not source:
            self.finished.emit(
                PipelineResult(
                    "",
                    "",
                    error=tr("pipe.ocr_empty", info=describe_image(img)),
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

        if not cfg.has_llm:
            self.progress.emit(tr("pipe.ocr_done_no_llm"))
            self._push_history(source, "")
            self.finished.emit(PipelineResult(source, "", ocr_only=True))
            return

        samp = self._sampling_fp(cfg)
        cache_key = self._make_cache_key(
            f"ocr|{source}",
            cfg.source_lang,
            cfg.target_lang,
            cfg.model,
            history,
            samp,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.progress.emit(tr("pipe.cache"))
            self._push_history(source, cached)
            self.finished.emit(PipelineResult(source, cached, from_cache=True))
            return

        if self._abort:
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
        translator = self._make_translator(cfg)
        translated = translator.translate(
            source,
            cfg.source_lang,
            cfg.target_lang,
            history=history,
        )
        self._cache.put(cache_key, translated)
        self._push_history(source, translated)
        self.progress.emit(tr("pipe.translate_done"))
        self.finished.emit(PipelineResult(source, translated, from_cache=False))

    def _run_vlm(self, cfg: AppConfig, img: Image.Image, *, force: bool) -> None:
        if not cfg.has_llm:
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
        samp = self._sampling_fp(cfg)
        cache_key = self._make_cache_key(
            f"vlm|{img_fp}",
            cfg.source_lang,
            cfg.target_lang,
            cfg.model,
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

        if self._abort:
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
        translator = self._make_translator(cfg)
        source, translated = translator.translate_image(
            img,
            cfg.source_lang,
            cfg.target_lang,
            history=history,
        )
        if not (translated or "").strip() and not (source or "").strip():
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

    @staticmethod
    def _make_translator(cfg: AppConfig) -> LLMTranslator:
        return LLMTranslator(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            custom_prompt=cfg.custom_prompt,
            protocol=getattr(cfg, "api_protocol", "openai") or "openai",
            anthropic_version=getattr(cfg, "anthropic_version", "2023-06-01"),
            max_tokens=int(getattr(cfg, "max_tokens", 2048) or 2048),
            temperature=getattr(cfg, "temperature", None),
            top_p=getattr(cfg, "top_p", None),
            top_k=getattr(cfg, "top_k", None),
            frequency_penalty=getattr(cfg, "frequency_penalty", None),
            presence_penalty=getattr(cfg, "presence_penalty", None),
            reasoning_effort=getattr(cfg, "reasoning_effort", "") or "",
            seed=getattr(cfg, "seed", None),
        )

    @staticmethod
    def _sampling_fp(cfg: AppConfig) -> str:
        return sampling_fingerprint(
            temperature=getattr(cfg, "temperature", None),
            top_p=getattr(cfg, "top_p", None),
            top_k=getattr(cfg, "top_k", None),
            frequency_penalty=getattr(cfg, "frequency_penalty", None),
            presence_penalty=getattr(cfg, "presence_penalty", None),
            reasoning_effort=getattr(cfg, "reasoning_effort", "") or "",
            seed=getattr(cfg, "seed", None),
            max_tokens=int(getattr(cfg, "max_tokens", 2048) or 2048),
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
        self._pending: PipelineJob | None = None

    @property
    def busy(self) -> bool:
        return self._busy

    def request(
        self,
        cfg: AppConfig,
        image: Image.Image | None = None,
        *,
        force: bool = False,
    ) -> None:
        # Snapshot config so mid-job UI mutations cannot race the worker
        job = PipelineJob(cfg=deepcopy(cfg), image=image, force=force)
        if self._busy:
            if self._pending is not None and self._pending.force:
                job.force = True
            self._pending = job
            return
        self._busy = True
        self.busy_changed.emit(True)
        self._submit.emit(job)

    def _on_finished(self, result: object) -> None:
        self.finished.emit(result)
        if self._pending is not None:
            job = self._pending
            self._pending = None
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
        self._pending = None
        self._cmd_abort.emit()
        # Allow the worker thread event loop to process abort + finish current slot
        self._thread.quit()
        if not self._thread.wait(10000):
            # Last resort: leave thread; process is exiting
            pass
