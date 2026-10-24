# qqexport

导出本机自有 QQ NT 账号的聊天记录。

> 仅限本人自有账号、自有设备、自有数据的本地备份用途。详见 [DISCLAIMER.md](DISCLAIMER.md)。

## 当前进度

P0：解密链路（定位数据目录 → 取密钥 → 解密 → 产出明文 SQLite）。
消息解析与 txt/html/pdf 导出在后续阶段。

## 开发环境

```
python -m pip install -e ".[dev]"
pytest
```

## 许可

MIT。第三方参照见 [NOTICE](NOTICE)。
