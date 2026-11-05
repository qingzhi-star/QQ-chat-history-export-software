"""应用目录与配置。所有可变状态都在 %LOCALAPPDATA%\\QQExport\\ 下。

QQEXPORT_HOME 环境变量可整体重定向，测试靠它把状态指向 tmp_path。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIR_NAME = "QQExport"


class ConfigError(RuntimeError):
    """配置不可读或损坏；消息不得包含配置内容或用户路径。"""


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

    def _validate(self) -> None:
        if (any(v is not None and not isinstance(v, str)
                for v in (self.data_dir, self.uin))
                or type(self.cache_key) is not bool
                or type(self.decode_voice) is not bool):
            raise ConfigError("config.json 字段类型错误，原文件未修改；请备份后修复配置。")

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        p = Path(path) if path is not None else config_path()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (ValueError, UnicodeError):
            raise ConfigError("config.json 已损坏，原文件未修改；请备份后修复配置。") from None
        except OSError:
            raise ConfigError("无法读取 config.json，原文件未修改；请检查访问权限。") from None
        if not isinstance(raw, dict):
            raise ConfigError("config.json 应为 JSON 对象，原文件未修改；请备份后修复配置。")
        known = set(cls.__dataclass_fields__)
        result = cls(**{k: v for k, v in raw.items() if k in known})
        result._validate()
        return result

    def save(self, path: Path | None = None) -> None:
        p = Path(path) if path is not None else config_path()
        self._validate()
        # 不允许用默认配置静默覆盖损坏的旧文件。
        self.load(p)
        tmp: Path | None = None
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=p.parent,
                    prefix=p.name + ".", suffix=".tmp", delete=False) as stream:
                tmp = Path(stream.name)
                json.dump(asdict(self), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            tmp.replace(p)
        except OSError:
            raise ConfigError("无法写入 config.json，原文件未修改；请检查访问权限和磁盘空间。") from None
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
