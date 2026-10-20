@echo off
rem AniSamplingMaster セットアップ（Windows）
rem uv の導入 → 依存ライブラリのインストール → デスクトップに起動アイコンを作成
chcp 65001 >nul
cd /d "%~dp0"

where uv >nul 2>nul
if not errorlevel 1 goto :sync
if exist "%USERPROFILE%\.local\bin\uv.exe" goto :addpath

echo uv（Python 環境管理ツール）が見つかりません。
choice /c YN /m "インストールしますか"
if errorlevel 2 goto :cancel
powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 goto :error

:addpath
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

:sync
echo 依存ライブラリをインストールしています…
uv sync
if errorlevel 1 goto :error
uv run python -m anisampler.shortcut
if errorlevel 1 goto :error
echo.
echo セットアップが完了しました。デスクトップの AniSamplingMaster アイコンから起動できます。
pause
exit /b 0

:cancel
echo 中止しました。
pause
exit /b 1

:error
echo セットアップに失敗しました。上のメッセージを確認してください。
pause
exit /b 1
