"""設定の読み書き（プロジェクト直下の config.json）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS = {
    "out_dir": str(Path.home() / "Music" / "AniSampling"),
    "device_name": "",
    "preroll_sec": 5.0,
    "template": "{now:%Y%m%d_%H%M%S}_{text}",
    "recognize": True,
    "model": "small",
    "language": "ja",
    "max_text_len": 40,
    "fallback_text": "音声なし",
    "save_txt": False,
    "hotkey": "ctrl+alt+r",
    "counter": 1,
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
