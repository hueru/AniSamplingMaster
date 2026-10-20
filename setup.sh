#!/usr/bin/env sh
# AniSamplingMaster セットアップ（macOS / Linux）
# uv の導入 → 依存ライブラリのインストール → デスクトップに起動アイコンを作成
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
  else
    printf 'uv（Python 環境管理ツール）が見つかりません。インストールしますか? [Y/n] '
    read -r ans
    case "$ans" in [nN]*) echo "中止しました"; exit 1 ;; esac
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
    else
      wget -qO- https://astral.sh/uv/install.sh | sh
    fi
    PATH="$HOME/.local/bin:$PATH"
  fi
fi

echo "依存ライブラリをインストールしています…"
uv sync
uv run python -m anisampler.shortcut
echo
echo "セットアップが完了しました。デスクトップの AniSamplingMaster アイコンから起動できます。"
