"""应用目录与配置。所有可变状态都在 %LOCALAPPDATA%\\QQExport\\ 下。

QQEXPORT_HOME 环境变量可整体重定向，测试靠它把状态指向 tmp_path。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass,field
import re
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
    last_account: str | None = None
    last_out: str | None = None
    formats: list[str] = field(default_factory=lambda:["html"])
    geometry: str = "1120x700"
    window_state: str = "normal"
    thumbnail_mb: int = 2048
    density: str = "normal"

    def _validate(self) -> None:
        if (any(v is not None and not isinstance(v, str)
                for v in (self.data_dir, self.uin))
                or type(self.cache_key) is not bool
                or type(self.decode_voice) is not bool):
            raise ConfigError("config.json 字段类型错误，原文件未修改；请备份后修复配置。")
        if (any(v is not None and not isinstance(v,str) for v in (self.last_account,self.last_out))
                or not isinstance(self.formats,list) or not self.formats
                or any(not isinstance(v,str) or v not in {"txt","html","pdf"} for v in self.formats)
                or not isinstance(self.geometry,str) or not re.fullmatch(r"\d{3,4}x\d{3,4}(?:[+-]\d{1,5}[+-]\d{1,5})?",self.geometry)
                or not isinstance(self.window_state,str) or self.window_state not in {"normal","zoomed"}
                or not isinstance(self.density,str) or self.density not in {"normal","compact"}
                or type(self.thumbnail_mb) is not int or not 0<=self.thumbnail_mb<=16384):
            raise ConfigError("config.json 界面设置无效，原文件未修改；请备份后恢复默认设置。")

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

    @classmethod
    def recover(cls,path: Path | None = None):
        """仅由显式恢复操作调用；先原样备份，再写默认配置，失败恢复旧文件。"""
        import uuid
        p = Path(path) if path is not None else config_path()
        backup = p.with_name(p.name+".bak-"+uuid.uuid4().hex)
        try:
            p.replace(backup)
            try:
                cls().save(p)
            except BaseException:
                backup.replace(p)
                raise
        except OSError:
            raise ConfigError("无法备份并恢复配置；原设置保留，请检查权限。") from None
        return backup
