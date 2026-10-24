"""应用目录与配置。所有可变状态都在 %LOCALAPPDATA%\\QQExport\\ 下。

QQEXPORT_HOME 环境变量可整体重定向，测试靠它把状态指向 tmp_path。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIR_NAME = "QQExport"


def app_dir() -> Path:
    override = os.environ.get("QQEXPORT_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / APP_DIR_NAME


def config_path() -> Path:
    return app_dir() / "config.json"


def keys_path() -> Path:
    return app_dir() / "keys.json"


def work_dir(uin: str) -> Path:
    return app_dir() / "work" / uin


@dataclass
class Config:
    data_dir: str | None = None
    uin: str | None = None
    cache_key: bool = True
    decode_voice: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        p = Path(path) if path is not None else config_path()
        if not p.is_file():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        p = Path(path) if path is not None else config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(p)
