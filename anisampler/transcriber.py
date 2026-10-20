"""faster-whisper による音声認識（CPU / オフライン）。"""
from __future__ import annotations

import wave

import numpy as np

WHISPER_RATE = 16000
MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
LANGUAGES = ["ja", "auto", "en", "zh", "ko"]


class Transcriber:
    def __init__(self, device: str = "cpu", compute_type: str = "int8") -> None:
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._model_name: str | None = None

    def is_loaded(self, name: str) -> bool:
        return self._model is not None and self._model_name == name

    def load(self, name: str) -> None:
        if self.is_loaded(name):
            return
        from faster_whisper import WhisperModel  # 起動を速くするため遅延 import

        self._model = None
        self._model = WhisperModel(name, device=self.device, compute_type=self.compute_type)
        self._model_name = name

    def transcribe(self, path: str, language: str = "ja") -> tuple[str, str]:
        """(認識テキスト, 言語コード) を返す。発話がなければテキストは空。"""
        segments, info = self._model.transcribe(
            load_wav_16k(path),
            language=None if language == "auto" else language,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        texts = []
        for seg in segments:
            # Whisper 標準の無音判定（BGM 等での幻聴テキストを除外）
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
                continue
            texts.append(seg.text)
        return "".join(texts).strip(), info.language


def load_wav_16k(path: str) -> np.ndarray:
    """16bit PCM WAV をモノラル float32 / 16kHz に変換する（PyAV に依存しない）。"""
    with wave.open(path, "rb") as w:
        ch, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("16bit PCM の WAV のみ対応しています")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    x = x.reshape(-1, ch).mean(axis=1)
    if rate == WHISPER_RATE or x.size == 0:
        return x
    if rate > WHISPER_RATE:
        # エイリアシング防止の窓付き sinc ローパス
        cutoff = WHISPER_RATE / rate * 0.45
        n = np.arange(-64, 65)
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(n.size)
        x = np.convolve(x, (h / h.sum()).astype(np.float32), mode="same")
    t_out = np.arange(0, x.size / rate, 1 / WHISPER_RATE)
    return np.interp(t_out, np.arange(x.size) / rate, x).astype(np.float32)
