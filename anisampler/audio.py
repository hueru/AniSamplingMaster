"""PC音声（ループバック）録音と遡り録音用リングバッファ。

soundcard ライブラリで Windows / Linux / macOS に対応する。
  Windows : WASAPI ループバック（スピーカー出力をそのまま取得）
  Linux   : PulseAudio / PipeWire のモニターソース
  macOS   : OS にループバック機能がないため BlackHole 等の仮想デバイスを入力として選ぶ
"""
from __future__ import annotations

import collections
import concurrent.futures
import queue
import sys
import threading
import time
import warnings
import wave
from dataclasses import dataclass

import numpy as np

SAMPLE_WIDTH = 2  # bytes (16bit PCM)
SAMPLE_RATE = 48000
BLOCK = 1024
# soundcard は blocksize をそのまま WASAPI のバッファ長に使う。BLOCK（約 21ms）のままだと、
# 受け取り側が一瞬止まっただけで音を取りこぼすため、Windows では余裕を持たせる。
DEVICE_BUFFER_SEC = 0.5 if sys.platform == "win32" else BLOCK / SAMPLE_RATE
PLAYER_BUFFER_SEC = 0.5 if sys.platform == "win32" else BLOCK / SAMPLE_RATE
# 何も再生されていない間 WASAPI ループバックはデータを返さないため、この時間以上データが
# 来なければ無音で埋めて時間軸を保つ
IDLE_FILL_SEC = 0.1


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
        self.is_loopback = False

        self._lock = threading.Lock()
        self._ring: collections.deque[bytes] = collections.deque()
        self._ring_frames = 0
        self._capture: threading.Thread | None = None
        self._processor: threading.Thread | None = None
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stop = threading.Event()
        self._last_silent = True
        self.glitches = 0       # モニタ開始以降に音を取りこぼした回数
        self.rec_glitches = 0   # 現在（直前）の録音中に取りこぼした回数
        self.error: str | None = None
        self._writer: wave.Wave_write | None = None
        self._writer_path: str | None = None
        self._rec_frames = 0

    def _import_sc(self) -> None:
        import soundcard
        self.sc = soundcard
        # WASAPI の「data discontinuity」は、バッファあふれで音を取りこぼした合図。
        # 無音から音が鳴り始めたときにも出るため、直前のブロックが無音なら数えない。
        warnings.filterwarnings("always", message="data discontinuity")
        original = warnings.showwarning

        def hook(message, category, filename, lineno, file=None, line=None):
            if "data discontinuity" in str(message):
                if not self._last_silent:
                    self.glitches += 1
                    if self.is_recording:
                        self.rec_glitches += 1
                return
            original(message, category, filename, lineno, file, line)

        warnings.showwarning = hook

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
        self.is_loopback = device.loopback
        self.error = None
        with self._lock:
            self._ring.clear()
            self._ring_frames = 0
        self._stop.clear()
        self._last_silent = True
        self._queue = queue.Queue()
        self._processor = threading.Thread(target=self._process_loop, args=(self._queue,), daemon=True)
        self._processor.start()
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
        if self._processor is not None:
            self._queue.put(None)
            self._processor.join(timeout=2)
            self._processor = None

    def clear_ring(self) -> None:
        """遡り用に保持している音声を破棄する。"""
        with self._lock:
            self._ring.clear()
            self._ring_frames = 0

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
        """デバイスから音を受け取ってキューに入れるだけにし、受け取りが止まらないようにする。"""
        try:
            buffer = int(self.rate * DEVICE_BUFFER_SEC)
            with mic.recorder(samplerate=self.rate, channels=self.channels, blocksize=buffer) as rec:
                started.set()
                wasapi = hasattr(rec, "_capture_available_frames")
                for block in (self._read_wasapi(rec) if wasapi else self._read_generic(rec)):
                    self._last_silent = not block.any()
                    self._queue.put(block)
        except Exception as e:
            self.error = f"録音デバイスエラー: {e}"
        finally:
            started.set()

    def _read_generic(self, rec):
        while not self._stop.is_set():
            yield rec.record(numframes=BLOCK)

    def _read_wasapi(self, rec):
        """WASAPI（Windows）用の読み取り。

        soundcard の record() は「データなし」を確認してから待つ間にスレッドが止まると、
        実際には音が届いていても確認し直さずに無音を差し込んでしまう（音の途切れになる）。
        ここでは毎回データの有無を確認し、本当にデバイスが音を出していない間だけ無音で埋める。
        """
        channels = len(set(rec.channelmap))
        idle_since = None
        while not self._stop.is_set():
            now = time.perf_counter()  # 先に時刻を取り、その後でデータの有無を確認する
            if rec._capture_available_frames() > 0:
                idle_since = None
                chunk = rec._record_chunk()  # データがあるので soundcard の無音補完は通らない
                if chunk.size:
                    yield chunk.reshape(-1, channels)
                continue
            # ここに来た時点で、前回読んでから now までにデバイスは何も出していない
            if idle_since is None:
                idle_since = now
            elif now - idle_since >= IDLE_FILL_SEC:
                frames = int((now - idle_since) * self.rate)
                idle_since += frames / self.rate
                yield np.zeros((frames, channels), dtype=np.float32)
            time.sleep(0.003)

    def _process_loop(self, q: queue.Queue) -> None:
        """変換・リングバッファ更新・ファイル書き込み（遅れても受け取りには影響しない）。"""
        while (block := q.get()) is not None:
            self._handle(block)

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
            self.rec_glitches = 0
            self._writer = w
            self._writer_path = path

    def stop_recording(self) -> tuple[str, float]:
        """録音を終了し (ファイルパス, 秒数) を返す。"""
        # 停止を押す前に受け取った音がまだキューに残っていれば、書き込みを待つ
        deadline = time.monotonic() + 0.3
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.005)
        with self._lock:
            w, path = self._writer, self._writer_path
            self._writer = self._writer_path = None
            seconds = self.recorded_seconds
            if w is not None:
                w.close()
        return path, seconds


def load_wav(path: str) -> tuple[np.ndarray, int]:
    """16bit PCM WAV を (float32 の [フレーム, チャンネル] 配列, サンプルレート) で返す。"""
    with wave.open(path, "rb") as w:
        ch, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("16bit PCM の WAV のみ再生できます")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return data.reshape(-1, ch), rate


class Player:
    """WAV ファイルを既定のスピーカーで再生する簡易プレーヤー。"""

    def __init__(self, sc) -> None:
        self.sc = sc
        self.path: str | None = None
        self.position = 0.0
        self.duration = 0.0
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def is_playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def play(self, path: str) -> None:
        self.stop()
        data, rate = load_wav(path)
        self.path, self.error = path, None
        self.position, self.duration = 0.0, len(data) / rate
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(data, rate), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=1)
            self._thread = None

    def _run(self, data: np.ndarray, rate: int) -> None:
        try:
            spk = self.sc.default_speaker()
            out_ch = 1 if spk.channels == 1 else 2
            if out_ch == 1:
                data = data.mean(axis=1, keepdims=True)
            elif data.shape[1] == 1:
                data = np.repeat(data, 2, axis=1)  # モノラルは左右両方から鳴らす
            else:
                data = data[:, :2]
            data = np.ascontiguousarray(data, dtype=np.float32)
            with spk.player(samplerate=rate, channels=out_ch, blocksize=int(rate * PLAYER_BUFFER_SEC)) as player:
                if hasattr(player, "_render_available_frames"):
                    self._play_wasapi(player, data, rate)
                else:
                    step = rate // 20  # 50ms ごとに停止要求を確認する
                    for i in range(0, len(data), step):
                        if self._stop.is_set():
                            break
                        player.play(data[i:i + step])
                        self.position = min(i + step, len(data)) / rate
        except Exception as e:
            self.error = f"再生エラー: {e}"

    def _play_wasapi(self, player, data: np.ndarray, rate: int) -> None:
        """WASAPI（Windows）用の再生。

        soundcard の play() は、渡したデータより出力バッファの空きが大きいと、空き全体を
        書き込んだことにして足りない分を無音で鳴らしてしまう（再生が途切れる）。
        ここでは空きとデータの小さい方だけを書き込み、最後はバッファが空になるまで待つ。
        """
        ffi = sys.modules[type(player).__module__]._ffi
        pos, total = 0, len(data)
        while not self._stop.is_set():
            if pos < total:
                n = min(player._render_available_frames(), total - pos)
                if n > 0:
                    chunk = data[pos:pos + n].tobytes()
                    ffi.memmove(player._render_buffer(n)[0], chunk, len(chunk))
                    player._render_release(n)
                    pos += n
            buffered = player.currentpadding  # 書き込み済みでまだ鳴っていないフレーム数
            self.position = max(0, pos - buffered) / rate
            if pos >= total and buffered == 0:
                break
            time.sleep(0.005)
