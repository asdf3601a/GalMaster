"""Capture → OCR → translate pipeline running on a worker thread."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PIL import Image
from PySide6.QtCore import QObject, Qt, QThread, Signal

from app.capture.screenshot import (
    capture_region,
    describe_image,
    is_mostly_blank,
    save_last_capture,
)
from app.config import AppConfig
from app.ocr.base import OCREngine, create_ocr_engine
from app.translate.cache import TranslationCache
from app.translate.llm_translator import LLMTranslator


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

    def __init__(self) -> None:
        super().__init__()
        self._ocr: OCREngine | None = None
        self._ocr_kind = ""
        self._cache = TranslationCache()
        self._last_source = ""
        # Sliding window of (source, translation) for LLM context
        self._history: deque[tuple[str, str]] = deque(maxlen=32)

    def run_job(self, job: object) -> None:
        if isinstance(job, PipelineJob):
            cfg = job.cfg
            img = job.image
            force = job.force
        else:
            # Backward-compatible: (cfg, image) via old path shouldn't happen
            self.finished.emit(PipelineResult("", "", error="內部錯誤：無效的工作項目"))
            return

        try:
            if img is None:
                if not cfg.has_region:
                    self.finished.emit(PipelineResult("", "", error="尚未框選 OCR 區域"))
                    return
                self.progress.emit("截圖中…")
                img = capture_region(
                    hwnd=cfg.bound_hwnd or None,
                    rel_x=cfg.region_x,
                    rel_y=cfg.region_y,
                    rel_w=cfg.region_w,
                    rel_h=cfg.region_h,
                )

            self.progress.emit(f"準備影像…（{describe_image(img)}）")
            debug_path = save_last_capture(img)
            if is_mostly_blank(img):
                self.finished.emit(
                    PipelineResult(
                        "",
                        "",
                        error=(
                            "截圖幾乎空白/全黑（可能被 Overlay 擋住、視窗內容未繪出、"
                            f"或框選區域不對）。{describe_image(img)}\n"
                            f"已存截圖：{debug_path} — 請打開確認是否看得到文字。"
                        ),
                    )
                )
                return

            ocr = self._get_ocr(cfg.ocr_engine, getattr(cfg, "source_lang", "ja") or "ja")
            backend = getattr(ocr, "backend_label", None) or cfg.ocr_engine
            self.progress.emit(f"OCR 辨識中（{backend}）…")
            source = ocr.recognize(img).strip()
            if not source:
                self.finished.emit(
                    PipelineResult(
                        "",
                        "",
                        error=(
                            "OCR 未辨識到文字。"
                            f"截圖 {describe_image(img)}\n"
                            f"已存：{debug_path}\n"
                            "請確認截圖裡有字；日文建議 OCR=自動/manga-ocr，並重新框選對話框。"
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
                        status_message="文字未變化（已略過翻譯）",
                    )
                )
                return
            self._last_source = source

            hist_n = max(0, int(getattr(cfg, "context_history_size", 3) or 0))
            history = list(self._history)[-hist_n:] if hist_n > 0 else []

            if not cfg.has_llm:
                self.progress.emit("OCR 完成（未設定 LLM）")
                # Still record source in history so later LLM turns have dialogue context
                self._push_history(source, "")
                self.finished.emit(PipelineResult(source, "", ocr_only=True))
                return

            cache_key = TranslationCache.make_key(
                source, cfg.source_lang, cfg.target_lang, cfg.model
            )
            # Cache only for zero-history or when force doesn't need fresh context styling
            if hist_n == 0:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    self.progress.emit("使用翻譯快取…")
                    self._push_history(source, cached)
                    self.finished.emit(PipelineResult(source, cached, from_cache=True))
                    return

            self.progress.emit(
                f"LLM 翻譯中…（上下文 {len(history)}/{hist_n} 則）"
                if hist_n
                else "LLM 翻譯中…"
            )
            translator = LLMTranslator(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=cfg.model,
                custom_prompt=cfg.custom_prompt,
                protocol=getattr(cfg, "api_protocol", "openai") or "openai",
                anthropic_version=getattr(cfg, "anthropic_version", "2023-06-01"),
                max_tokens=int(getattr(cfg, "max_tokens", 2048) or 2048),
            )
            translated = translator.translate(
                source,
                cfg.source_lang,
                cfg.target_lang,
                history=history,
            )
            if hist_n == 0:
                self._cache.put(cache_key, translated)
            self._push_history(source, translated)
            self.progress.emit("翻譯完成")
            self.finished.emit(PipelineResult(source, translated, from_cache=False))
        except Exception as exc:
            self.finished.emit(PipelineResult("", "", error=f"管線錯誤：{exc}"))

    def _push_history(self, source: str, translation: str) -> None:
        source = (source or "").strip()
        if not source:
            return
        self._history.append((source, (translation or "").strip()))

    def _get_ocr(self, kind: str, lang: str = "ja") -> OCREngine:
        key = f"{kind}:{lang}"
        if self._ocr is None or self._ocr_kind != key:
            self.progress.emit(f"載入 OCR 引擎（{kind}）…")
            self._ocr = create_ocr_engine(kind, lang=lang)
            self._ocr_kind = key
            label = getattr(self._ocr, "backend_label", None)
            if label:
                self.progress.emit(f"OCR 引擎就緒：{label}")
        return self._ocr

    def reset_dedupe(self) -> None:
        self._last_source = ""

    def clear_history(self) -> None:
        self._history.clear()

    def clear_cache(self) -> None:
        self._cache.clear()


class TranslationPipeline(QObject):
    """Serializes pipeline jobs on a background QThread."""

    finished = Signal(object)
    progress = Signal(str)
    busy_changed = Signal(bool)
    _submit = Signal(object)  # PipelineJob

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker = _Worker()
        self._worker.moveToThread(self._thread)
        self._submit.connect(self._worker.run_job, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._on_finished)
        self._worker.progress.connect(self.progress)
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
        job = PipelineJob(cfg=cfg, image=image, force=force)
        if self._busy:
            # Keep latest pending; prefer force=True if either is force
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
        self._worker.reset_dedupe()

    def clear_history(self) -> None:
        self._worker.clear_history()

    def clear_cache(self) -> None:
        self._worker.clear_cache()
        self._worker.clear_history()

    def shutdown(self) -> None:
        self._thread.quit()
        self._thread.wait(3000)
