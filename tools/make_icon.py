"""アプリアイコン (assets/icon.png / icon.ico / icon.icns) を生成する。

外部画像ライブラリを使わず、numpy の距離関数で描画して PNG を直接書き出す。
  uv run python tools/make_icon.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TOP = np.array([108, 59, 209]) / 255     # 紫
BOTTOM = np.array([232, 69, 123]) / 255  # ピンク
REC = np.array([255, 59, 48]) / 255
BARS = [0.30, 0.55, 0.85, 0.62, 0.95, 0.50, 0.28]  # 波形バーの高さ（相対）


def _round_rect(px, py, cx, cy, hw, hh, r):
    qx = np.abs(px - cx) - (hw - r)
    qy = np.abs(py - cy) - (hh - r)
    outside = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
    return outside + np.minimum(np.maximum(qx, qy), 0) - r


def _cover(d, px_size):
    return np.clip(0.5 - d / px_size, 0, 1)[..., None]


def render(size: int) -> np.ndarray:
    """size x size の RGBA (uint8) を返す。座標は 0..1 の正規化座標。"""
    px = 1.0 / size
    y, x = (np.mgrid[0:size, 0:size] + 0.5) * px
    bg = _cover(_round_rect(x, y, 0.5, 0.5, 0.46, 0.46, 0.2), px)
    color = TOP + (BOTTOM - TOP) * y[..., None]

    bars = np.zeros_like(bg)
    n, w = len(BARS), 0.062
    gap = 0.56 / (n - 1)
    for i, h in enumerate(BARS):
        cx = 0.5 - 0.28 + i * gap
        d = _round_rect(x, y, cx, 0.47, w / 2, 0.30 * h, w / 2)
        bars = np.maximum(bars, _cover(d, px))
    color = color + (1 - color) * bars

    # 右下の録音ボタン（白い縁 + 赤丸）
    d = np.hypot(x - 0.76, y - 0.76)
    color = color + (1 - color) * _cover(d - 0.125, px)
    color = color + (REC - color) * _cover(d - 0.085, px)

    rgba = np.concatenate([color, bg], axis=-1)
    return (np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8)


def png_bytes(img: np.ndarray) -> bytes:
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[row].tobytes() for row in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico_bytes(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    pngs = [png_bytes(render(s)) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for s, data in zip(sizes, pngs):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(pngs)


def icns_bytes() -> bytes:
    body = b""
    for tag, s in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)):
        data = png_bytes(render(s))
        body += tag + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "icon.png").write_bytes(png_bytes(render(256)))
    (ASSETS / "icon.ico").write_bytes(ico_bytes())
    (ASSETS / "icon.icns").write_bytes(icns_bytes())
    print("generated:", *sorted(p.name for p in ASSETS.iterdir()))


if __name__ == "__main__":
    main()
