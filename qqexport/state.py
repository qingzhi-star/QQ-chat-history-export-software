"""应用状态的原子 JSON 写入；不用于隐藏保存正文。"""
import json
import os
from pathlib import Path
import tempfile


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,
                                         prefix=path.name+".",suffix=".tmp",delete=False) as stream:
            temp = Path(stream.name)
            json.dump(value,stream,ensure_ascii=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
