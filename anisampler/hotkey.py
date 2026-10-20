"""pynput によるグローバルホットキー（Windows / macOS / Linux X11）。

macOS ではターミナル（または Python）に「アクセシビリティ」と「入力監視」の許可が必要。
Linux の Wayland セッションではグローバルホットキーは使えない（ウィンドウ内のボタンを使う）。
"""
from __future__ import annotations

from typing import Callable

_MODS = {"ctrl": "<ctrl>", "control": "<ctrl>", "alt": "<alt>", "option": "<alt>",
         "shift": "<shift>", "win": "<cmd>", "cmd": "<cmd>", "super": "<cmd>"}
_KEYS = {"space", "enter", "tab", "esc", "pause", "insert", "delete", "home", "end",
         "page_up", "page_down", "scroll_lock", "left", "up", "right", "down",
         *(f"f{i}" for i in range(1, 21))}
_ALIASES = {"pageup": "page_up", "pagedown": "page_down", "scrolllock": "scroll_lock"}


class HotkeyError(ValueError):
    pass


def to_pynput(spec: str) -> str:
    """'ctrl+alt+r' 形式を pynput の '<ctrl>+<alt>+r' 形式に変換する。"""
    parts = [p for p in spec.lower().replace(" ", "").split("+") if p]
    if not parts:
        raise HotkeyError("ホットキーが空です")
    out = []
    for p in parts[:-1]:
        if p not in _MODS:
            raise HotkeyError(f"不明な修飾キー: {p}")
        out.append(_MODS[p])
    key = _ALIASES.get(parts[-1], parts[-1])
    if key in _KEYS:
        out.append(f"<{key}>")
    elif len(key) == 1 and key.isalnum():
        out.append(key)
    else:
        raise HotkeyError(f"不明なキー: {key}")
    return "+".join(out)


class GlobalHotkey:
    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        self.combo = to_pynput(spec)
        self.callback = callback
        self._listener = None

    def start(self) -> None:
        try:
            from pynput import keyboard
            self._listener = keyboard.GlobalHotKeys({self.combo: self.callback})
            self._listener.start()
        except Exception as e:  # X サーバーなし・権限不足など
            raise HotkeyError(f"グローバルホットキーを使用できません: {e}") from e

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
