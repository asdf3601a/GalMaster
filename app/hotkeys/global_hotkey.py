"""Windows global hotkey via RegisterHotKey + Qt native event filter."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

user32 = ctypes.windll.user32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

# Common virtual-key codes
_VK = {
    **{c: ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{str(i): 0x30 + i for i in range(10)},
    "F1": 0x70,
    "F2": 0x71,
    "F3": 0x72,
    "F4": 0x73,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "SPACE": 0x20,
    "TAB": 0x09,
    "ESCAPE": 0x1B,
    "ESC": 0x1B,
}


@dataclass(frozen=True)
class HotkeySpec:
    modifiers: int
    vk: int
    display: str


def parse_hotkey(text: str) -> HotkeySpec:
    """Parse strings like 'Ctrl+Shift+T' into Win32 modifiers + VK."""
    parts = [p.strip() for p in (text or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        raise ValueError("熱鍵不可為空")

    mods = 0
    key: str | None = None
    for p in parts:
        u = p.upper()
        if u in ("CTRL", "CONTROL", "CONTROLKEY"):
            mods |= MOD_CONTROL
        elif u in ("SHIFT",):
            mods |= MOD_SHIFT
        elif u in ("ALT", "MENU"):
            mods |= MOD_ALT
        elif u in ("WIN", "META", "SUPER", "WINDOWS"):
            mods |= MOD_WIN
        else:
            key = u

    if not key:
        raise ValueError(f"無法解析熱鍵: {text}")
    if key not in _VK:
        if len(key) == 1:
            _VK[key] = ord(key)
        else:
            raise ValueError(f"不支援的按鍵: {key}")
    return HotkeySpec(modifiers=mods | MOD_NOREPEAT, vk=_VK[key], display=text)


class GlobalHotkeyFilter(QAbstractNativeEventFilter, QObject):
    """Register one global hotkey and emit `activated` on press."""

    activated = Signal()

    def __init__(self, parent: QObject | None = None, hotkey_id: int = 1) -> None:
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._hotkey_id = hotkey_id
        self._registered = False
        self._hwnd = 0

    def register(self, hwnd: int, hotkey: str) -> None:
        self.unregister()
        spec = parse_hotkey(hotkey)
        self._hwnd = int(hwnd)
        ok = user32.RegisterHotKey(
            self._hwnd,
            self._hotkey_id,
            spec.modifiers,
            spec.vk,
        )
        if not ok:
            raise OSError(f"RegisterHotKey 失敗（可能被佔用）: {hotkey}")
        self._registered = True

    def unregister(self) -> None:
        if self._registered and self._hwnd:
            user32.UnregisterHotKey(self._hwnd, self._hotkey_id)
        self._registered = False

    def nativeEventFilter(self, eventType, message):  # noqa: N802
        # PySide6: eventType is bytes/str, message is sip.voidptr
        try:
            et = bytes(eventType).decode() if isinstance(eventType, (bytes, bytearray)) else str(eventType)
        except Exception:
            et = str(eventType)
        if et not in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            return False, 0
        try:
            msg = wintypes.MSG.from_address(int(message))
        except Exception:
            return False, 0
        if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
            self.activated.emit()
            return True, 0
        return False, 0
