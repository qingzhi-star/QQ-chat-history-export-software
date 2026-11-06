"""P2 合成规模基准；只打印时间/内存数字，不读取本机 QQ。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import tracemalloc
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qqexport.export.session import ExportSource
from qqexport.qq import contacts, media
from qqexport.repository import MessageRepository
from tests.helpers import synth_db as sd, synth_proto as sp


def dataset(root, count):
    db = sd.create_msg_db(root / "synthetic.db")
    con = sqlite3.connect(db)
    blob = sp.message(sp.text_element("synthetic benchmark message"))
    con.executemany('INSERT INTO c2c_msg_table ("40001","40011","40020","40021","40050","40800") '
                    'VALUES (?,2,\'u_a\',\'u_a\',?,?)', ((i+1,i//3,blob) for i in range(count)))
    con.commit()
    con.close()
    return ExportSource.from_parts(db, contacts.Contacts.load(None, None, account_uin="12345"),
                                  media.build_index(root / "media"), "12345")


def run(count):
    with tempfile.TemporaryDirectory(prefix="qqexport-bench-") as temp:
        source = dataset(Path(temp), count)
        tracemalloc.start()
        start = time.perf_counter()
        repo = MessageRepository(source)
        repo.window("c2c:u_a", 0)
        opening = time.perf_counter() - start
        start = time.perf_counter()
        found = sum(1 for _ in repo.iter_messages("c2c:u_a"))
        streaming = time.perf_counter() - start
        peak = tracemalloc.get_traced_memory()[1] / (1024 * 1024)
        tracemalloc.stop()
        repo.close()
        assert found == count
        return {"messages": count, "open_first_page_ms": round(opening*1000, 2),
                "stream_seconds": round(streaming, 3), "python_peak_mib": round(peak, 2)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=100000)
    args = parser.parse_args()
    print(json.dumps(run(args.messages)))
