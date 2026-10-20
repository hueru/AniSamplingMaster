import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# デスクトップアイコンから起動した場合（--log 指定、または pythonw でコンソールがない場合）は
# 出力を app.log に書き出す
if "--log" in sys.argv or sys.stdout is None or sys.stderr is None:
    sys.stdout = sys.stderr = open(Path(__file__).resolve().parent / "app.log", "w",
                                   encoding="utf-8", buffering=1)


def _run() -> None:
    try:
        from anisampler.gui import main
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            from tkinter import Tk, messagebox
            root = Tk()
            root.withdraw()
            messagebox.showerror("AniSamplingMaster", f"起動に失敗しました:\n\n{traceback.format_exc(limit=3)}")
            root.destroy()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    _run()
