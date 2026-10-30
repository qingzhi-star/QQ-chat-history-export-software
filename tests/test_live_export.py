"""真机导出验收：单格式、固定大目录、只读、确定性、清理。"""
from __future__ import annotations

import codecs
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

from qqexport import config
from qqexport.core import discover, keystore
from qqexport.export import orchestrator
from qqexport.export.session import ExportSource

pytestmark = pytest.mark.live


def _identity(path: Path):
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def _account_key():
    cfg = config.Config.load()
    account = next(a for a in discover.discover_accounts() if a.uin == cfg.uin)
    key = keystore.KeyStore().load(account.uin)
    if key is None:
        pytest.skip("没有缓存口令")
    return account, key


def test_live_export_html_txt_only_requested_files():
    account, key = _account_key()
    watched = [account.msg_db, account.db_dir / "profile_info.db",
               account.db_dir / "group_info.db"]
    before = {p: _identity(p) for p in watched if p.is_file()}
    now = datetime(2026, 10, 30, 12, 0, 0)

    with tempfile.TemporaryDirectory(prefix="qqexport-live-export-") as td:
        base = Path(td)
        with ExportSource.open(account, key) as src:
            chats = src.chats()
            c2c = sorted([c for c in chats if c.kind == "c2c"],
                         key=lambda c: c.messages, reverse=True)[:1]
            group = sorted([c for c in chats if c.kind == "group"],
                           key=lambda c: c.messages, reverse=True)[:1]
            picked = c2c + group
            assert len(picked) == 2
            ids = [c.chat_id for c in picked]

            rep = orchestrator.export_chats(
                src, ids, orchestrator.ExportOptions(
                    format="html", out_base=base, now=now))
            root = base / orchestrator.ROOT_FOLDER
            assert rep.chats_exported == 2 and rep.messages_total > 0
            latest = max(m.ts for c in picked for m in src.iter_messages(c.chat_id))
            assert latest > time.time() - 30 * 86400
            for info in picked:
                folder = root / orchestrator.folder_name(info)
                assert {p.name for p in folder.iterdir()} == {"chat.html", "media"}
                html = (folder / "chat.html").read_text(encoding="utf-8")
                assert not __import__("re").search(
                    r'(?:src|href)="https?://|url\(https?://|//cdn', html)

            # 同快照 + 同固定时钟 → 字节一致
            base2 = base / "again"
            orchestrator.export_chats(
                src, ids, orchestrator.ExportOptions(
                    format="html", out_base=base2, now=now))
            for info in picked:
                name = orchestrator.folder_name(info)
                assert (root / name / "chat.html").read_bytes() == \
                       (base2 / orchestrator.ROOT_FOLDER / name / "chat.html").read_bytes()

            # txt 只写 chat.txt
            base3 = base / "txt"
            orchestrator.export_chats(
                src, ids[:1], orchestrator.ExportOptions(
                    format="txt", out_base=base3, now=now))
            folder = base3 / orchestrator.ROOT_FOLDER / orchestrator.folder_name(picked[0])
            assert {p.name for p in folder.iterdir()} == {"chat.txt"}
            assert (folder / "chat.txt").read_bytes().startswith(codecs.BOM_UTF8)

        assert not list(base.rglob("*.partial-*"))

    after = {p: _identity(p) for p in watched if p.is_file()}
    assert after == before, "源数据库在导出期间被修改"
