"""デスクトップアイコン用の起動スクリプト（コンソールを開かずに起動する）。

アイコンからは uv が管理する Python 本体の pythonw.exe でこのファイルを実行する。
（uv の .venv\Scripts\pythonw.exe は内部でコンソール版 python.exe を起動するため、
 ターミナルが開いてしまう。）venv のライブラリを読み込んでから main.py を実行する。
"""
import runpy
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"

if sys.platform == "win32":
    site.addsitedir(str(VENV / "Lib" / "site-packages"))
else:
    for sp in (VENV / "lib").glob("python*/site-packages"):
        site.addsitedir(str(sp))

sys.path.insert(0, str(ROOT))
sys.argv = [str(ROOT / "main.py"), "--log", *sys.argv[1:]]
runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
