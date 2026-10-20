"""AniSamplingMaster の GUI (tkinter)。"""
from __future__ import annotations

import math
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from . import config
from .audio import Device, Recorder
from .hotkey import GlobalHotkey, HotkeyError
from .naming import build_filename, prepare_text, unique_path
from .transcriber import LANGUAGES, MODELS, Transcriber

TEMPLATE_HELP = (
    "{now:%Y%m%d_%H%M%S} 日時（strftime書式）  {date} {time}  {text} 認識テキスト  "
    "{text:.10} 先頭10文字  {n:04d} 連番  {dur:.1f} 秒数  {lang} 言語  {device} デバイス  "
    "「/」でサブフォルダ"
)
FONT = {"win32": "Yu Gothic UI", "darwin": "Hiragino Sans"}.get(sys.platform, "Noto Sans CJK JP")


def open_path(path: str, reveal: bool = False) -> None:
    """フォルダを開く / ファイルをファイルマネージャーで表示する（OS別）。"""
    if sys.platform == "win32":
        if reveal:
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
        else:
            os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if reveal else ["open", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if reveal else path])


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = config.load()
        self.recorder = Recorder()
        self.transcriber = Transcriber(self.cfg["whisper_device"], self.cfg["whisper_compute_type"])
        self.ui_q: queue.Queue[tuple] = queue.Queue()
        self.jobs: queue.Queue[dict | None] = queue.Queue()
        self.devices: dict[str, Device] = {}
        self.hotkey: GlobalHotkey | None = None
        self.current: dict | None = None
        self.row_paths: dict[str, str] = {}
        self.closing = False
        self._meter = 0.0
        self._shown_error: str | None = None

        self._build()
        self._refresh_devices()
        self._apply_hotkey(show_error=False)
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        if self.cfg["recognize"]:
            self.jobs.put({"type": "preload", "model": self.cfg["model"]})
        self._update_preview()
        self._poll()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================================================================ UI
    def _build(self) -> None:
        c = self.cfg
        root = self.root
        root.title("AniSamplingMaster")
        root.minsize(760, 560)
        style = ttk.Style()
        style.configure(".", font=(FONT, 10))
        style.configure("Hint.TLabel", foreground="#666", font=(FONT, 9))

        self.v_outdir = tk.StringVar(value=c["out_dir"])
        self.v_device = tk.StringVar()
        self.v_preroll = tk.StringVar(value=str(c["preroll_sec"]))
        self.v_template = tk.StringVar(value=c["template"])
        self.v_recognize = tk.BooleanVar(value=c["recognize"])
        self.v_model = tk.StringVar(value=c["model"])
        self.v_lang = tk.StringVar(value=c["language"])
        self.v_maxlen = tk.StringVar(value=str(c["max_text_len"]))
        self.v_fallback = tk.StringVar(value=c["fallback_text"])
        self.v_savetxt = tk.BooleanVar(value=c["save_txt"])
        self.v_hotkey = tk.StringVar(value=c["hotkey"])
        self.v_preview = tk.StringVar()
        self.v_status = tk.StringVar(value="準備完了")
        self.v_elapsed = tk.StringVar(value="00:00.0")

        pad = {"padx": 6, "pady": 3}

        # --- 保存設定
        f = ttk.LabelFrame(root, text="保存設定")
        f.pack(fill="x", padx=8, pady=(8, 4))
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="保存先").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.v_outdir).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(f, text="参照…", command=self._browse).grid(row=0, column=2, **pad)
        ttk.Button(f, text="開く", command=self._open_folder).grid(row=0, column=3, **pad)
        ttk.Label(f, text="命名規則").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.v_template).grid(row=1, column=1, columnspan=3, sticky="ew", **pad)
        ttk.Label(f, text=TEMPLATE_HELP, style="Hint.TLabel", wraplength=700).grid(
            row=2, column=1, columnspan=3, sticky="w", padx=6)
        ttk.Label(f, text="プレビュー").grid(row=3, column=0, sticky="w", **pad)
        ttk.Label(f, textvariable=self.v_preview).grid(row=3, column=1, columnspan=3, sticky="w", **pad)

        # --- 録音設定
        f = ttk.LabelFrame(root, text="録音設定")
        f.pack(fill="x", padx=8, pady=4)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="入力デバイス").grid(row=0, column=0, sticky="w", **pad)
        self.cb_device = ttk.Combobox(f, textvariable=self.v_device, state="readonly")
        self.cb_device.grid(row=0, column=1, columnspan=4, sticky="ew", **pad)
        self.cb_device.bind("<<ComboboxSelected>>", lambda e: self._open_device())
        self.btn_refresh = ttk.Button(f, text="更新", command=self._refresh_devices)
        self.btn_refresh.grid(row=0, column=5, **pad)
        ttk.Label(f, text="遡り秒数").grid(row=1, column=0, sticky="w", **pad)
        sp = ttk.Frame(f)
        sp.grid(row=1, column=1, sticky="w")
        ttk.Spinbox(sp, from_=0, to=120, increment=1, width=6, textvariable=self.v_preroll).pack(side="left", **pad)
        ttk.Label(sp, text="秒前から録音（0で無効）", style="Hint.TLabel").pack(side="left")
        ttk.Label(f, text="ホットキー").grid(row=1, column=2, sticky="e", **pad)
        ttk.Entry(f, textvariable=self.v_hotkey, width=16).grid(row=1, column=3, **pad)
        ttk.Button(f, text="適用", command=self._apply_hotkey).grid(row=1, column=4, **pad)

        # --- 音声認識
        f = ttk.LabelFrame(root, text="音声認識（ファイル名に使用）")
        f.pack(fill="x", padx=8, pady=4)
        ttk.Checkbutton(f, text="有効", variable=self.v_recognize).grid(row=0, column=0, sticky="w", **pad)
        ttk.Label(f, text="モデル").grid(row=0, column=1, sticky="e", **pad)
        cb = ttk.Combobox(f, textvariable=self.v_model, values=MODELS, width=14, state="readonly")
        cb.grid(row=0, column=2, sticky="w", **pad)
        cb.bind("<<ComboboxSelected>>", lambda e: self._preload_model())
        ttk.Label(f, text="言語").grid(row=0, column=3, sticky="e", **pad)
        ttk.Combobox(f, textvariable=self.v_lang, values=LANGUAGES, width=6, state="readonly").grid(
            row=0, column=4, sticky="w", **pad)
        ttk.Checkbutton(f, text="認識テキストを .txt でも保存", variable=self.v_savetxt).grid(
            row=0, column=5, sticky="w", **pad)
        ttk.Label(f, text="最大文字数").grid(row=1, column=1, sticky="e", **pad)
        ttk.Spinbox(f, from_=0, to=150, width=6, textvariable=self.v_maxlen).grid(row=1, column=2, sticky="w", **pad)
        ttk.Label(f, text="発話なしの時").grid(row=1, column=3, sticky="e", **pad)
        ttk.Entry(f, textvariable=self.v_fallback, width=14).grid(row=1, column=4, columnspan=2, sticky="w", **pad)
        ttk.Label(f, text="※小さいモデルほど高速・低精度。初回はモデルをダウンロードします",
                  style="Hint.TLabel").grid(row=2, column=0, columnspan=6, sticky="w", padx=6)

        # --- 録音操作
        f = ttk.Frame(root)
        f.pack(fill="x", padx=8, pady=6)
        self.btn_rec = tk.Button(f, text="● 録音開始", width=14, font=(FONT, 14, "bold"),
                                 bg="#d93025", fg="white", activebackground="#b3261e",
                                 activeforeground="white", relief="flat", command=self.toggle_record)
        self.btn_rec.pack(side="left", padx=(0, 10))
        ttk.Label(f, textvariable=self.v_elapsed, font=(FONT, 16)).pack(side="left", padx=6)
        lv = ttk.Frame(f)
        lv.pack(side="left", fill="x", expand=True, padx=10)
        ttk.Label(lv, text="入力レベル", style="Hint.TLabel").pack(anchor="w")
        self.meter = ttk.Progressbar(lv, maximum=100)
        self.meter.pack(fill="x")
        ttk.Label(root, textvariable=self.v_status, style="Hint.TLabel").pack(fill="x", padx=10)

        # --- 履歴
        f = ttk.Frame(root)
        f.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.tree = ttk.Treeview(f, columns=("time", "file", "text"), show="headings", height=8)
        for col, label, w in (("time", "時刻", 70), ("file", "ファイル", 300), ("text", "認識テキスト", 300)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=w, stretch=col != "time")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._reveal_selected)
        ttk.Label(root, text="ダブルクリックでファイルの場所を表示", style="Hint.TLabel").pack(anchor="e", padx=10)

        for v in (self.v_template, self.v_maxlen, self.v_fallback):
            v.trace_add("write", lambda *a: self._update_preview())
        self.v_preroll.trace_add("write", lambda *a: self._apply_preroll())
        self._apply_preroll()

    # ============================================================ 設定値
    def _num(self, var: tk.StringVar, default: float, lo: float, hi: float) -> float:
        try:
            return min(hi, max(lo, float(var.get())))
        except ValueError:
            return default

    def _collect_cfg(self) -> dict:
        self.cfg.update(
            out_dir=self.v_outdir.get(),
            device_name=self.v_device.get(),
            preroll_sec=self._num(self.v_preroll, 5.0, 0, 120),
            template=self.v_template.get(),
            recognize=self.v_recognize.get(),
            model=self.v_model.get(),
            language=self.v_lang.get(),
            max_text_len=int(self._num(self.v_maxlen, 40, 0, 150)),
            fallback_text=self.v_fallback.get(),
            save_txt=self.v_savetxt.get(),
            hotkey=self.v_hotkey.get(),
        )
        return self.cfg

    def _save_cfg(self) -> None:
        try:
            config.save(self._collect_cfg())
        except OSError as e:
            self.v_status.set(f"設定を保存できません: {e}")

    def _apply_preroll(self) -> None:
        self.recorder.preroll_sec = self._num(self.v_preroll, 5.0, 0, 120)

    def _update_preview(self) -> None:
        maxlen = int(self._num(self.v_maxlen, 40, 0, 150))
        try:
            rel = build_filename(self.v_template.get(), started=datetime.now(),
                                 text=prepare_text("サンプルのセリフです", maxlen),
                                 n=self.cfg["counter"], dur=7.5, lang="ja",
                                 device=self.v_device.get())
            self.v_preview.set(rel)
        except (KeyError, ValueError, IndexError) as e:
            self.v_preview.set(f"⚠ 命名規則が不正です: {e!r}")

    # ============================================================ デバイス
    def _refresh_devices(self) -> None:
        try:
            devs = self.recorder.list_devices()
        except Exception as e:
            messagebox.showerror("エラー", f"デバイス一覧を取得できません:\n{e}")
            return
        self.devices = {d.label: d for d in devs}
        labels = list(self.devices)
        self.cb_device["values"] = labels
        want = self.v_device.get() or self.cfg["device_name"]
        if want not in self.devices:
            default_id = self.recorder.default_device_id()
            want = next((d.label for d in devs if d.id == default_id), labels[0] if labels else "")
        self.v_device.set(want)
        self._open_device()

    def _open_device(self) -> None:
        name = self.v_device.get()
        if name not in self.devices:
            self.v_status.set("入力デバイスがありません")
            return
        try:
            self.recorder.start_monitor(self.devices[name])
            if sys.platform == "darwin" and not self.devices[name].loopback and "blackhole" not in name.lower():
                self.v_status.set("macOS で PC 音声を録るには BlackHole 等の仮想デバイスを選んでください")
                return
            self.v_status.set(f"モニタ中: {name}  ({self.recorder.rate} Hz / {self.recorder.channels} ch)")
        except Exception as e:
            self.v_status.set(f"デバイスを開けません: {e}")
        self._update_preview()

    # ============================================================ ホットキー
    def _apply_hotkey(self, show_error: bool = True) -> None:
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None
        spec = self.v_hotkey.get().strip()
        if not spec:
            return
        try:
            hk = GlobalHotkey(spec, lambda: self.ui_q.put(("hotkey",)))
            hk.start()
            self.hotkey = hk
            self.v_status.set(f"ホットキー {spec} で録音開始/停止")
        except HotkeyError as e:
            if show_error:
                messagebox.showwarning("ホットキー", str(e))
            else:
                self.v_status.set(f"ホットキー: {e}")

    # ============================================================ 録音
    def toggle_record(self) -> None:
        if self.recorder.is_recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self) -> None:
        cfg = self._collect_cfg()
        try:
            build_filename(cfg["template"], started=datetime.now(), text="x")
        except (KeyError, ValueError, IndexError) as e:
            messagebox.showerror("命名規則", f"命名規則が不正です: {e!r}")
            return
        out_dir = cfg["out_dir"]
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("保存先", f"保存先フォルダを作成できません:\n{e}")
            return
        started = datetime.now()
        tmp = os.path.join(out_dir, f"~rec_{started:%Y%m%d_%H%M%S_%f}.wav")
        try:
            self.recorder.start_recording(tmp)
        except Exception as e:
            messagebox.showerror("録音", f"録音を開始できません:\n{e}")
            return
        self.current = {"type": "rec", "tmp": tmp, "started": started, "device": self.recorder.device_name}
        self.btn_rec.configure(text="■ 停止", bg="#444", activebackground="#222")
        self.cb_device.configure(state="disabled")
        self.btn_refresh.configure(state="disabled")
        self.v_status.set("録音中…")

    def _stop_record(self) -> None:
        tmp, dur = self.recorder.stop_recording()
        meta = self.current or {}
        self.current = None
        cfg = self._collect_cfg()
        meta.update(
            dur=dur, n=cfg["counter"],
            settings={k: cfg[k] for k in ("out_dir", "template", "recognize", "model", "language",
                                          "max_text_len", "fallback_text", "save_txt")},
        )
        cfg["counter"] += 1
        self._save_cfg()
        meta["iid"] = self.tree.insert("", 0, values=(meta["started"].strftime("%H:%M:%S"),
                                                      os.path.basename(tmp), "処理中…"))
        self.row_paths[meta["iid"]] = tmp
        self.jobs.put(meta)
        self.btn_rec.configure(text="● 録音開始", bg="#d93025", activebackground="#b3261e")
        self.cb_device.configure(state="readonly")
        self.btn_refresh.configure(state="normal")
        self.v_status.set(f"録音停止（{dur:.1f} 秒）")
        self._update_preview()

    def _preload_model(self) -> None:
        if self.v_recognize.get():
            self.jobs.put({"type": "preload", "model": self.v_model.get()})

    # ============================================================ ワーカー
    def _worker(self) -> None:
        """認識とリネームをバックグラウンドで順番に処理する。"""
        while True:
            job = self.jobs.get()
            if job is None:
                break
            if job["type"] == "preload":
                if not self.closing:
                    self._load_model(job["model"])
                continue
            text, lang, err = "", "", None
            s = job["settings"]
            if s["recognize"] and not self.closing:
                try:
                    if self._load_model(s["model"]):
                        self.ui_q.put(("status", "音声認識中…"))
                        text, lang = self.transcriber.transcribe(job["tmp"], s["language"])
                except Exception as e:
                    err = f"音声認識に失敗: {e}"
            try:
                path = self._finalize(job, text, lang)
                self.ui_q.put(("saved", job["iid"], path, text, err))
            except Exception as e:
                self.ui_q.put(("saved", job["iid"], job["tmp"], text, f"保存に失敗: {e}"))
        self.ui_q.put(("worker_done",))

    def _load_model(self, name: str) -> bool:
        if self.transcriber.is_loaded(name):
            return True
        self.ui_q.put(("status", f"モデル {name} を読み込み中…（初回はダウンロードします）"))
        try:
            self.transcriber.load(name)
            self.ui_q.put(("status", f"モデル {name} 準備完了"))
            return True
        except Exception as e:
            self.ui_q.put(("status", f"モデルを読み込めません: {e}"))
            return False

    def _finalize(self, job: dict, text: str, lang: str) -> str:
        s = job["settings"]
        name_text = prepare_text(text, s["max_text_len"]) or prepare_text(s["fallback_text"], s["max_text_len"])
        kwargs = dict(started=job["started"], text=name_text, n=job["n"], dur=job["dur"],
                      lang=lang, device=job["device"])
        try:
            rel = build_filename(s["template"], **kwargs)
        except (KeyError, ValueError, IndexError):
            rel = build_filename(config.DEFAULTS["template"], **kwargs)
        path = unique_path(s["out_dir"], rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.replace(job["tmp"], path)
        if s["save_txt"] and text:
            with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as fp:
                fp.write(text + "\n")
        return path

    # ============================================================ ポーリング
    def _poll(self) -> None:
        peak, self.recorder.peak = self.recorder.peak, 0.0
        db = 20 * math.log10(peak) if peak > 1e-6 else -120
        self._meter = max((db + 60) / 60 * 100, self._meter * 0.85, 0)
        self.meter["value"] = self._meter
        if self.recorder.error and self.recorder.error != self._shown_error:
            self._shown_error = self.recorder.error
            self.v_status.set(self.recorder.error)
        if self.recorder.is_recording:
            sec = self.recorder.recorded_seconds
            self.v_elapsed.set(f"{int(sec // 60):02d}:{sec % 60:04.1f}")

        while True:
            try:
                msg = self.ui_q.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "hotkey" and not self.closing:
                self.toggle_record()
            elif kind == "status":
                self.v_status.set(msg[1])
            elif kind == "saved":
                _, iid, path, text, err = msg
                self.row_paths[iid] = path
                if self.tree.exists(iid):
                    self.tree.item(iid, values=(self.tree.set(iid, "time"),
                                                os.path.relpath(path, self.cfg["out_dir"]),
                                                text or "（発話なし）"))
                self.v_status.set(err or f"保存しました: {os.path.basename(path)}")
            elif kind == "worker_done":
                self._shutdown()
                return
        self.root.after(50, self._poll)

    # ============================================================ その他
    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.v_outdir.get() or None)
        if d:
            self.v_outdir.set(os.path.normpath(d))

    def _open_folder(self) -> None:
        d = self.v_outdir.get()
        os.makedirs(d, exist_ok=True)
        open_path(d)

    def _reveal_selected(self, _event=None) -> None:
        sel = self.tree.selection()
        if sel and (path := self.row_paths.get(sel[0])) and os.path.exists(path):
            open_path(path, reveal=True)

    def _on_close(self) -> None:
        if self.closing:
            return
        if self.recorder.is_recording:
            self._stop_record()
        self._save_cfg()
        self.closing = True  # 未処理の認識はスキップし、保存だけ済ませて終了
        self.v_status.set("終了処理中…")
        self.jobs.put(None)

    def _shutdown(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
        self.recorder.close()
        self.root.destroy()


def main() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
