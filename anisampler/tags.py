"""WAV にメタデータ（タグ）を埋め込む。

DAW や素材管理ソフトで検索できるよう、2 種類のタグを同時に書き込む。
  RIFF INFO : LIST/INFO チャンク（INAM=タイトル, ICMT=コメント, ICRD=日付, ISFT=ソフト名）
              data チャンクの後ろに置く。Windows のエクスプローラーは fmt と data の間にある
              INFO を読まず、文字列をシステムの文字コード（日本語版なら CP932）として解釈する。
              そのため Windows ではシステムの文字コード、それ以外では UTF-8 で書き込む
  ID3v2.3   : "id3 " チャンク（TIT2=タイトル, COMM=コメント, TYER=年, TSSE=ソフト名）
              文字列は UTF-16（BOM 付き）で文字コードの曖昧さがない。ファイル末尾に置く
"""
from __future__ import annotations

import os
import struct
import sys
from datetime import datetime

SOFTWARE = "AniSamplingMaster"
_ISO639_2 = {"ja": "jpn", "en": "eng", "zh": "zho", "ko": "kor"}


def _info_encoding() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            return f"cp{ctypes.windll.kernel32.GetACP()}"  # 日本語版 Windows では cp932
        except Exception:
            pass
    return "utf-8"


INFO_ENCODING = _info_encoding()


# ---------------------------------------------------------------- RIFF
def _read_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("WAV ファイルではありません")
    chunks, pos = [], 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        chunks.append((cid, data[pos + 8:pos + 8 + size]))
        pos += 8 + size + (size & 1)  # チャンクは偶数バイト境界にそろえる
    return chunks


def _chunk(cid: bytes, body: bytes) -> bytes:
    return cid + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")


def _info_chunk(fields: dict[bytes, str]) -> bytes:
    body = b"INFO"
    for key, value in fields.items():
        if value:
            # システムの文字コードで表せない文字（絵文字など）は ? になる。ID3 側には元の文字のまま入る
            body += _chunk(key, value.encode(INFO_ENCODING, errors="replace") + b"\x00")
    return _chunk(b"LIST", body)


# ---------------------------------------------------------------- ID3v2.3
def _utf16(text: str) -> bytes:
    return text.encode("utf-16")  # BOM 付き


def _id3_frame(fid: bytes, body: bytes) -> bytes:
    return fid + struct.pack(">I", len(body)) + b"\x00\x00" + body


def _id3_text(fid: bytes, text: str) -> bytes:
    return _id3_frame(fid, b"\x01" + _utf16(text) + b"\x00\x00")


def _id3_comment(text: str, lang: str) -> bytes:
    # エンコーディング(1) + 言語(3) + 説明（空）+ 終端 + 本文
    return _id3_frame(b"COMM", b"\x01" + lang.encode("ascii") + _utf16("") + b"\x00\x00"
                      + _utf16(text) + b"\x00\x00")


def _syncsafe(n: int) -> bytes:
    return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))


def _id3_chunk(title: str, comment: str, date: datetime, language: str) -> bytes:
    frames = b""
    if title:
        frames += _id3_text(b"TIT2", title)
    if comment:
        frames += _id3_comment(comment, _ISO639_2.get(language, "und"))
    frames += _id3_text(b"TYER", date.strftime("%Y"))
    frames += _id3_text(b"TSSE", SOFTWARE)
    tag = b"ID3" + b"\x03\x00" + b"\x00" + _syncsafe(len(frames)) + frames
    return _chunk(b"id3 ", tag)


# ---------------------------------------------------------------- 書き込み
def write_tags(path: str, *, title: str, comment: str, date: datetime, language: str = "") -> None:
    """WAV ファイルにタグを書き込む（既存の INFO / ID3 タグは置き換える）。"""
    with open(path, "rb") as f:
        chunks = _read_chunks(f.read())

    def is_old_tag(cid: bytes, body: bytes) -> bool:
        return (cid == b"LIST" and body[:4] == b"INFO") or cid.lower() == b"id3 "

    body = b"WAVE"
    for cid, data in chunks:
        if not is_old_tag(cid, data):
            body += _chunk(cid, data)
    body += _info_chunk({b"INAM": title, b"ICMT": comment,
                         b"ICRD": date.strftime("%Y-%m-%d"), b"ISFT": SOFTWARE})
    body += _id3_chunk(title, comment, date, language)

    tmp = path + ".tagtmp"
    with open(tmp, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
    os.replace(tmp, path)
