"""T6：伴随库（profile_info / group_info / rich_media）快照 + 解密。

它们与主库同 key / 同 SQLCipher 参数、同样有 1024 字节自定义头。
复用 P0 的 snapshot / detect_params / decrypt_file，不改既有 core 行为。
"""
from __future__ import annotations

from pathlib import Path

from . import decrypt as _decrypt
from . import snapshot as _snapshot
from . import sqlcipher as _sc


class CompanionError(RuntimeError):
    """伴随库缺失、损坏或口令不匹配。消息不含路径/口令。"""


def decrypt_companion(src_db: Path, passphrase: bytes, dst_dir: Path) -> Path | None:
    """把任意伴随库解密成明文 SQLite；源文件不存在返回 None。"""
    if not src_db.is_file():
        return None
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        snap = _snapshot.snapshot(
            src_db, dst_dir / f"snap-{src_db.name}",
            validator=_snapshot.make_validator(passphrase))
    except Exception as exc:                      # 快照失败（含权限/一致性问题）
        raise CompanionError(f"companion snapshot failed: {type(exc).__name__}") from exc

    header = _sc.read_header_and_page1(snap.db_path)
    if _sc.detect_params(passphrase, header) is None:
        raise CompanionError("companion cipher detection failed")

    out = dst_dir / f"plain-{src_db.name}"
    result = _decrypt.decrypt_file(
        snap.db_path, passphrase, out,
        wal_path=snap.wal_path, shm_path=snap.shm_path, overwrite=True)
    if result.status == "failed" or not out.is_file():
        raise CompanionError(f"companion decrypt failed ({result.status})")
    return out
