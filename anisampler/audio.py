"""PC音声（ループバック）録音と遡り録音用リングバッファ。

soundcard ライブラリで Windows / Linux / macOS に対応する。
  Windows : WASAPI ループバック（スピーカー出力をそのまま取得）
  Linux   : PulseAudio / PipeWire のモニターソース
  macOS   : OS にループバック機能がないため BlackHole 等の仮想デバイスを入力として選ぶ
"""
from __future__ import annotations

import collections
import concurrent.futures
import threading
import wave
from dataclasses import dataclass

import numpy as np

SAMPLE_WIDTH = 2  # bytes (16bit PCM)
SAMPLE_RATE = 48000
BLOCK = 1024


@dataclass
class Device:
    label: str       # 画面表示名
    name: str        # soundcard 上のデバイス名
    id: str
    loopback: bool
    channels: int


class Recorder:
    """入力デバイスを常時モニタし、直近 preroll_sec 秒を保持する。

    start_recording() で保持していた直近の音声をファイル先頭に書き込み、
    以降の音声を stop_recording() まで追記する。
    """

    def __init__(self) -> None:
        # Windows では soundcard の import 時に呼び出しスレッドが COM(MTA) 初期化される。
        # tkinter のメインスレッドに影響させないよう、専用スレッドで import・操作する。
        self._sc_thread = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._sc_thread.submit(self._import_sc).result()

        self.preroll_sec = 5.0
        self.peak = 0.0  # 前回読み出し以降のピーク (0..1)
        self.rate = SAMPLE_RATE
        self.channels = 2
        self.device_name = ""

        self._lock = threading.Lock()
        self._ring: collections.deque[bytes] = collections.deque()
        self._ring_frames = 0
        self._capture: threading.Thread | None = None
        self._stop = threading.Event()
        self.error: str | None = None
        self._writer: wave.Wave_write | None = None
        self._writer_path: str | None = None
        self._rec_frames = 0

    def _import_sc(self) -> None:
        import warnings

        import soundcard
        # 無音区間の前後で毎回出る「data discontinuity」警告は無害なので抑制する
        warnings.filterwarnings("ignore", message="data discontinuity")
        self.sc = soundcard

    # ---- デバイス -------------------------------------------------------
    def list_devices(self) -> list[Device]:
        """PC音声（ループバック）→ マイク等の順で返す。"""
        def _list():
            mics = self.sc.all_microphones(include_loopback=True)
            devs = [Device(f"[{'PC音声' if m.isloopback else '入力'}] {m.name}",
                           m.name, str(m.id), bool(m.isloopback), int(m.channels)) for m in mics]
            return sorted(devs, key=lambda d: not d.loopback)
        return self._sc_thread.submit(_list).result()

    def default_device_id(self) -> str | None:
        """既定スピーカーのループバックを返す（なければ既定マイク）。"""
        def _default():
            try:
                spk = self.sc.default_speaker()
                return str(self.sc.get_microphone(spk.name, include_loopback=True).id)
            except Exception:
                try:
                    return str(self.sc.default_microphone().id)
                except Exception:
                    return None
        return self._sc_thread.submit(_default).result()

    def start_monitor(self, device: Device) -> None:
        self.stop_monitor()
        mic = self._sc_thread.submit(
            self.sc.get_microphone, device.id, include_loopback=True).result()
        self.channels = max(1, min(int(mic.channels), 8))
        self.device_name = device.name
        self.error = None
        with self._lock:
            self._ring.clear()
            self._ring_frames = 0
        self._stop.clear()
        started = threading.Event()
        self._capture = threading.Thread(target=self._capture_loop, args=(mic, started), daemon=True)
        self._capture.start()
        started.wait(3)
        if self.error:
            raise RuntimeError(self.error)

    def stop_monitor(self) -> None:
        if self._capture is not None:
            self._stop.set()
            self._capture.join(timeout=2)
            self._capture = None

    @property
    def monitoring(self) -> bool:
        return self._capture is not None and self._capture.is_alive()

    def close(self) -> None:
        if self.is_recording:
            self.stop_recording()
        self.stop_monitor()
        self._sc_thread.shutdown(wait=False)

    # ---- 音声処理 -------------------------------------------------------
    def _capture_loop(self, mic, started: threading.Event) -> None:
        try:
            with mic.recorder(samplerate=self.rate, channels=self.channels, blocksize=BLOCK) as rec:
                started.set()
                while not self._stop.is_set():
                    self._handle(rec.record(numframes=BLOCK))
        except Exception as e:
            self.error = f"録音デバイスエラー: {e}"
        finally:
            started.set()

    def _handle(self, block: np.ndarray) -> None:
        if block.ndim == 1:
            block = block[:, None]
        if block.size:
            self.peak = max(self.peak, float(np.abs(block).max()))
        pcm = (np.clip(block, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        n = block.shape[0]
        frame_bytes = self.channels * SAMPLE_WIDTH
        with self._lock:
            self._ring.append(pcm)
            self._ring_frames += n
            keep = int(self.preroll_sec * self.rate)
            while self._ring and self._ring_frames - len(self._ring[0]) // frame_bytes >= keep:
                self._ring_frames -= len(self._ring.popleft()) // frame_bytes
            if self._writer is not None:
                self._writer.writeframes(pcm)
                self._rec_frames += n

    # ---- 録音 -----------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._writer is not None

    @property
    def recorded_seconds(self) -> float:
        return self._rec_frames / self.rate

    def start_recording(self, path: str) -> None:
        if not self.monitoring:
            raise RuntimeError(self.error or "入力デバイスが開かれていません")
        frame_bytes = self.channels * SAMPLE_WIDTH
        with self._lock:
            w = wave.open(path, "wb")
            w.setnchannels(self.channels)
            w.setsampwidth(SAMPLE_WIDTH)
            w.setframerate(self.rate)
            need = int(self.preroll_sec * self.rate) * frame_bytes
            pre = b"".join(self._ring)[-need:] if need > 0 else b""
            w.writeframes(pre)
            self._rec_frames = len(pre) // frame_bytes
            self._writer = w
            self._writer_path = path

    def stop_recording(self) -> tuple[str, float]:
        """録音を終了し (ファイルパス, 秒数) を返す。"""
        with self._lock:
            w, path = self._writer, self._writer_path
            self._writer = self._writer_path = None
            seconds = self.recorded_seconds
            if w is not None:
                w.close()
        return path, seconds
