"""
Windows AI TextRecognizer — same OCR stack as Win11 Snipping Tool \"Text actions\".

Implementation:
  1. Bootstrap Windows App SDK (Microsoft.WindowsAppRuntime.Bootstrap.dll)
  2. Invoke tools/wasdk_ocr/WinAiOcr.ps1 which calls
     Microsoft.Windows.AI.Imaging.TextRecognizer

Notes:
  - Requires Windows 11 with Windows App Runtime + AI Imaging components.
  - Some machines return Access Denied for *unpackaged* processes; Snipping Tool
    is a packaged Store app and always has identity. When AI OCR is blocked,
    raise so the factory can fall back to classic Windows.Media.Ocr.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from app.config import project_root
from app.ocr.preprocess import preprocess_for_ocr


def _tools_dir() -> Path:
    return project_root() / "tools" / "wasdk_ocr"


def _bootstrap_dll() -> Path | None:
    d = _tools_dir()
    for p in (
        d / "Microsoft.WindowsAppRuntime.Bootstrap.dll",
        project_root() / "tools" / "wasdk" / "Microsoft.WindowsAppRuntime.Bootstrap.dll",
    ):
        if p.is_file():
            return p
    return None


def windows_ai_ocr_available() -> bool:
    """True when host script + bootstrap DLL are present (does not prove AI unlock)."""
    if sys.platform != "win32":
        return False
    host = _tools_dir() / "WinAiOcr.ps1"
    return host.is_file() and _bootstrap_dll() is not None


def _normalize_cjk_spaces(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(
        r"(?<=[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef])\s+(?=[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef])",
        "",
        s,
    )
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def recognize_with_windows_ai(image: Image.Image, *, timeout: float = 120.0) -> str:
    """
    Run Snipping-Tool-style Windows AI OCR on a PIL image.

    Raises RuntimeError on failure (including Access Denied).
    """
    if sys.platform != "win32":
        raise RuntimeError("Windows AI OCR is only available on Windows")

    host = _tools_dir() / "WinAiOcr.ps1"
    boot = _bootstrap_dll()
    if not host.is_file():
        raise RuntimeError(f"Missing OCR host script: {host}")
    if boot is None:
        raise RuntimeError(
            "Missing Microsoft.WindowsAppRuntime.Bootstrap.dll under tools/wasdk_ocr/"
        )

    # Ensure bootstrap sits next to the script (SetDllDirectory relative)
    boot_next = _tools_dir() / "Microsoft.WindowsAppRuntime.Bootstrap.dll"
    if not boot_next.is_file() and boot.is_file():
        try:
            boot_next.write_bytes(boot.read_bytes())
        except OSError:
            pass

    img = image.convert("RGB")
    # Light preprocess: keep natural colors for the AI model
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="galmaster_winai_", suffix=".png")
        os.close(fd)
        img.save(tmp, format="PNG")

        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(host),
            "-ImagePath",
            tmp,
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(_tools_dir()),
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode == 0 and stdout:
            return _normalize_cjk_spaces(stdout)

        if proc.returncode == 20 or "Access Denied" in stderr or "拒絕" in stderr:
            raise PermissionError(
                "Windows AI OCR 拒絕存取（未封裝行程）。"
                "剪取工具是 Store 套件、具 package identity；"
                "一般 exe/Python 可能被系統擋下此 API。\n"
                + (stderr[:400] if stderr else "")
            )
        raise RuntimeError(
            f"Windows AI OCR failed (code={proc.returncode}): "
            f"{stderr or stdout or 'no output'}"
        )
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


class WindowsAIOCREngine:
    """Snipping Tool style OCR via Windows AI TextRecognizer."""

    name = "windows_ai"

    def __init__(self, lang: str = "auto") -> None:
        self._preferred = lang or "auto"
        if not windows_ai_ocr_available():
            raise RuntimeError(
                "Windows AI OCR 主機未就緒（缺 Bootstrap.dll 或 WinAiOcr.ps1）。"
            )
        # Probe once: keep failure for first recognize with image is fine
        self._last_error = ""

    @property
    def backend_label(self) -> str:
        return "Windows AI OCR（剪取工具同款）"

    def recognize(self, image: Image.Image) -> str:
        best = ""
        errors: list[str] = []
        # Try natural + inverted for dark game text
        for force_invert in (None, False, True):
            prepared = preprocess_for_ocr(image, force_invert=force_invert)
            try:
                text = recognize_with_windows_ai(prepared)
            except PermissionError:
                raise
            except Exception as exc:
                errors.append(str(exc))
                text = ""
            text = _normalize_cjk_spaces(text)
            if len(text) > len(best):
                best = text
        if best:
            return best
        if errors:
            raise RuntimeError(errors[-1])
        return ""
