"""T12：真机导出验收（@pytest.mark.live，默认跳过）。"""
from __future__ import annotations

import codecs
import os
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


def _find_account_key():
    cfg = config.Config.load()
    accounts = discover.discover_accounts()
    account = next(a for a in accounts if a.uin == cfg.uin)
    key = keystore.KeyStore().load(account.uin)
    if key is None:
        pytest.skip("没有缓存口令")
    return account, key


def test_live_export_c2c_and_group_all_formats():
    account, key = _find_account_key()
    watched = [account.msg_db, account.db_dir / "profile_info.db",
               account.db_dir / "group_info.db"]
    before = {p: _identity(p) for p in watched if p.is_file()}
    now = datetime(2026, 10, 30, 12, 0, 0)

    with tempfile.TemporaryDirectory(prefix="qqexport-live-export-") as td:
        out1 = Path(td) / "out1"
        with ExportSource.open(account, key) as src:
            chats = src.chats()
            c2c = sorted([c for c in chats if c.kind == "c2c"],
                         key=lambda c: c.messages, reverse=True)[:1]
            group = sorted([c for c in chats if c.kind == "group"],
                           key=lambda c: c.messages, reverse=True)[:1]
            picked = c2c + group
            assert len(picked) == 2, "需要至少一个私聊和一个群聊"
            rep = orchestrator.export_chats(
                src, [c.chat_id for c in picked],
                orchestrator.ExportOptions(formats={"txt", "html"},
                                           out_dir=out1, now=now))
            assert rep.chats_exported == 2
            assert rep.consistent and rep.messages_total > 0
            latest = 0
            for c in picked:
                for m in src.iter_messages(c.chat_id):
                    latest = max(latest, m.ts)
            assert latest > time.time() - 30 * 86400, "最近的消息不在产物里"

            # 同一快照 + 同一时钟 → HTML/txt 字节一致
            out2 = Path(td) / "out2"
            rep2 = orchestrator.export_chats(
                src, [c.chat_id for c in picked],
                orchestrator.ExportOptions(formats={"txt", "html"},
                                           out_dir=out2, now=now))
            assert rep2.chats_exported == 2
            for chat in picked:
                slug = orchestrator.chat_slug(chat)
                for name in ("chat.html", "chat.txt"):
                    if name == "chat.html" and not (out1 / account.uin / slug / name).is_file():
                        continue
                    assert (out1 / account.uin / slug / name).read_bytes() == \
                           (out2 / account.uin / slug / name).read_bytes()

        assert not list(Path(td).rglob("*.partial-*"))
        for c in picked:
            slug = orchestrator.chat_slug(c)
            html = (out1 / account.uin / slug / "chat.html").read_text(encoding="utf-8")
            assert not __import__("re").search(
                r'(?:src|href)="https?://|url\(https?://|//cdn', html)
            txt = (out1 / account.uin / slug / "chat.txt").read_bytes()
            assert txt.startswith(codecs.BOM_UTF8)
        assert (out1 / "report.json").is_file()

    after = {p: _identity(p) for p in watched if p.is_file()}
    assert after == before, "源数据库在导出期间被修改"
