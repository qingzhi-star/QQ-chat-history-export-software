# qqexport

![tests](https://github.com/qingzhi-star/QQ-chat-history-export-software/actions/workflows/tests.yml/badge.svg)

导出本机自有 QQ NT 账号的聊天记录。

> 仅限本人自有账号、自有设备、自有数据的本地备份用途。详见 [DISCLAIMER.md](DISCLAIMER.md)。

## 当前进度

**P0 已完成**（2026-10-29）。解密链路：定位数据目录 → 取密钥 → 解密 → 产出
明文 SQLite。本机实测（Windows 11 x64 / Python 3.14.6 / QQ NT 9.9.36-53644）：
doctor 11/11、`quick_check == ok`、消息表可查询、WAL 回放有页级证据、
同一快照两次解密一致、源目录只读性未发现删除/改写；发布轮子在干净 venv 中
`qqexport version` / `key show` 退出码 0。详见
[docs/compat-matrix.md](docs/compat-matrix.md)。

**发布状态：尚未发布。** 发布门槛是 P1（消息解析 + txt/html/pdf 导出）完成后
的人工验收；在此之前不打 tag、不创建 Release。发布工作流已就绪
（`.github/workflows/release.yml`），但只在功能完整后由人工打 `v*` tag 触发。

**兼容性**：`requires-python >=3.12`；CI 在 `windows-latest` 上验证
**3.12 / 3.13 / 3.14** 三条腿（测试 + 全库隐私巡检全绿）。真实 QQ 的 live
冒烟与人工审查过的页 1 fixture 不进入 CI，只在本地跑。

真库页 1 fixture 已于 2026-10-29 采集并审查：页 1 解密后 63/4096 非零字节，
可打印 ASCII 只有 `SQLite format 3` 与 6 字节碎片，无数字串、无中文；
fixture 不含表结构（主库 page 1 的 schema 尚未 checkpoint，在 WAL 里），
它钉的是 1024 字节头 / salt / HMAC 与解开后的 SQLite 魔数。

消息解析与 txt/html/pdf 导出属 P1+，计划尚未编写。

## 安装

```
python -m pip install .
# 或（合并到默认分支后）
python -m pip install "git+https://github.com/qingzhi-star/QQ-chat-history-export-software.git"
```

## 开发环境

```
python -m pip install -e ".[dev]"
python -m pytest            # 默认不含 live / 需要 fixture 的项会 SKIP
python -m pytest -m live -v -s   # 真机冒烟（需要 QQ 已登录或已缓存口令）
```

常用命令（`qqexport` 不在 PATH 时用 `python -m qqexport`）：

```
python -m qqexport version
python -m qqexport doctor [--uin UIN] [--scan] [--ascii]
python -m qqexport key scan [--uin UIN]
python -m qqexport key set [--uin UIN] [--from-stdin]
python -m qqexport key show
python -m qqexport key clear [--uin UIN]
```

## 许可

MIT。第三方参照见 [NOTICE](NOTICE)。
