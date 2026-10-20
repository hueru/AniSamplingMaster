"""デスクトップ（とアプリメニュー）に起動用アイコンを作成する。

  uv run python -m anisampler.shortcut           作成
  uv run python -m anisampler.shortcut --remove  削除

  Windows : デスクトップとスタートメニューに .lnk（コンソールなしで起動）
  macOS   : デスクトップに AniSamplingMaster.app
  Linux   : デスクトップとアプリメニューに .desktop
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

APP = "AniSamplingMaster"
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
MAIN = ROOT / "main.py"
LAUNCHER = ROOT / "launch.pyw"  # Windows 用（コンソールなしで起動）
BUNDLE_ID = "io.github.hueru.anisamplingmaster"


def _try(*cmd: str) -> None:
    """補助コマンドを実行する（コマンドがない・失敗した場合は無視）。"""
    try:
        subprocess.run(cmd, capture_output=True, check=False)
    except OSError:
        pass


# ---------------------------------------------------------------- Windows
def _win_desktop() -> Path:
    # OneDrive でデスクトップがリダイレクトされている場合にも対応する
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True, encoding="utf-8")
    path = out.stdout.strip()
    return Path(path) if path else Path.home() / "Desktop"


def _win_targets() -> list[Path]:
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return [_win_desktop() / f"{APP}.lnk", start_menu / f"{APP}.lnk"]


def install_windows() -> list[Path]:
    # uv の .venv\Scripts\pythonw.exe は内部でコンソール版 python.exe を起動し、
    # ターミナルが開いてしまう。Python 本体の pythonw.exe で launch.pyw を実行する。
    pythonw = Path(sys.base_prefix) / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ASM_LNK);"
        "$s.TargetPath = $env:ASM_TARGET; $s.Arguments = '\"' + $env:ASM_MAIN + '\"';"
        "$s.WorkingDirectory = $env:ASM_ROOT; $s.IconLocation = $env:ASM_ICON + ',0';"
        "$s.Description = 'PC音声の遡り録音ツール'; $s.Save()"
    )
    created = []
    for lnk in _win_targets():
        lnk.parent.mkdir(parents=True, exist_ok=True)
        # パスに日本語や空白が含まれても崩れないよう環境変数で渡す
        env = dict(os.environ, ASM_LNK=str(lnk), ASM_TARGET=str(pythonw), ASM_MAIN=str(LAUNCHER),
                   ASM_ROOT=str(ROOT), ASM_ICON=str(ASSETS / "icon.ico"))
        subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True)
        created.append(lnk)
    return created


# ---------------------------------------------------------------- macOS
def _mac_targets() -> list[Path]:
    return [Path.home() / "Desktop" / f"{APP}.app"]


def install_macos() -> list[Path]:
    app = _mac_targets()[0]
    if app.exists():
        shutil.rmtree(app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    shutil.copy(ASSETS / "icon.icns", app / "Contents" / "Resources" / "icon.icns")
    (app / "Contents" / "Info.plist").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{APP}</string>
  <key>CFBundleDisplayName</key><string>{APP}</string>
  <key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string>
  <key>CFBundleExecutable</key><string>{APP}</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>PC音声（仮想オーディオデバイス）を録音するために使用します。</string>
</dict>
</plist>
""", encoding="utf-8")
    launcher = app / "Contents" / "MacOS" / APP
    launcher.write_text(
        "#!/bin/sh\n"
        f"cd {shlex.quote(str(ROOT))} || exit 1\n"
        f"exec {shlex.quote(sys.executable)} {shlex.quote(str(MAIN))} --log\n", encoding="utf-8")
    launcher.chmod(0o755)
    _try("touch", str(app))  # Finder のアイコン表示を更新
    return [app]


# ---------------------------------------------------------------- Linux
def _xdg_desktop() -> Path:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except FileNotFoundError:
        pass
    return Path.home() / "Desktop"


def _linux_targets() -> list[Path]:
    name = f"{APP.lower()}.desktop"
    apps = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications"
    return [apps / name, _xdg_desktop() / name]


def _desktop_quote(arg: str) -> str:
    for ch in ('\\', '"', "`", "$"):
        arg = arg.replace(ch, "\\" + ch)
    return f'"{arg}"'


def install_linux() -> list[Path]:
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP}\n"
        "Comment=PC音声の遡り録音ツール\n"
        f"Exec={_desktop_quote(sys.executable)} {_desktop_quote(str(MAIN))} --log\n"
        f"Path={ROOT}\n"
        f"Icon={ASSETS / 'icon.png'}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;Recorder;\n"
        f"StartupWMClass={APP}\n"
    )
    created = []
    for path in _linux_targets():
        if path.parent.name != "applications" and not path.parent.is_dir():
            continue  # デスクトップフォルダがない環境ではメニュー登録のみ
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry, encoding="utf-8")
        path.chmod(0o755)
        # GNOME で「信頼されていない起動ファイル」扱いにならないようにする
        _try("gio", "set", str(path), "metadata::trusted", "true")
        created.append(path)
    _try("update-desktop-database", str(_linux_targets()[0].parent))
    return created


# ---------------------------------------------------------------- 共通
def targets() -> list[Path]:
    if sys.platform == "win32":
        return _win_targets()
    if sys.platform == "darwin":
        return _mac_targets()
    return _linux_targets()


def install() -> list[Path]:
    if sys.platform == "win32":
        return install_windows()
    if sys.platform == "darwin":
        return install_macos()
    return install_linux()


def remove() -> list[Path]:
    removed = []
    for path in targets():
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            continue
        removed.append(path)
    return removed


def main() -> None:
    if "--remove" in sys.argv:
        paths = remove()
        print("削除しました:" if paths else "削除するアイコンはありません")
    else:
        paths = install()
        print("起動アイコンを作成しました:")
    for p in paths:
        print("  ", p)


if __name__ == "__main__":
    main()
