"""命名規則テンプレートからファイル名を組み立てる。

テンプレートは Python の str.format 書式。使用可能なキー:
  {now:%Y%m%d_%H%M%S}  録音開始日時 (strftime 書式)
  {date} / {time}      YYYYMMDD / HHMMSS
  {text}               認識したセリフ ({text:.10} で先頭10文字)
  {n:04d}              連番
  {dur:.1f}            録音秒数
  {lang}               認識言語 (ja, en ...)
  {device}             録音デバイス名
"/" を含めるとサブフォルダになる。
"""
from __future__ import annotations

import os
import re
from datetime import datetime

# Windows でファイル名に使えない文字は全角に置き換える
_FULLWIDTH = str.maketrans({
    "\\": "＼", "/": "／", ":": "：", "*": "＊", "?": "？",
    '"': "”", "<": "＜", ">": "＞", "|": "｜",
})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
MAX_STEM = 150


def sanitize(s: str) -> str:
    s = _CONTROL.sub("", s.translate(_FULLWIDTH))
    return _SPACES.sub(" ", s).strip()


def prepare_text(text: str, max_len: int) -> str:
    text = sanitize(text)
    if max_len > 0:
        text = text[:max_len]
    return text.rstrip(" .")


def build_filename(template: str, *, started: datetime, text: str, n: int = 1,
                   dur: float = 0.0, lang: str = "", device: str = "") -> str:
    """テンプレートを展開し、保存先フォルダからの相対パス (.wav 付き) を返す。

    テンプレートが不正な場合は KeyError / ValueError / IndexError を送出する。
    """
    rendered = template.format(
        now=started, date=started.strftime("%Y%m%d"), time=started.strftime("%H%M%S"),
        text=text, n=n, dur=dur, lang=lang, device=sanitize(device),
    )
    parts = []
    for part in re.split(r"[\\/]", rendered):
        part = sanitize(part).strip(" ._-")
        if not part or part in (".", ".."):
            continue
        if part.upper() in _RESERVED:
            part = f"_{part}"
        parts.append(part)
    if not parts:
        parts = [started.strftime("%Y%m%d_%H%M%S")]
    parts[-1] = parts[-1][:MAX_STEM].rstrip(" .")
    return os.path.join(*parts) + ".wav"


def unique_path(folder: str, rel: str) -> str:
    path = os.path.join(folder, rel)
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(path):
        path = f"{stem}_{i}{ext}"
        i += 1
    return path
