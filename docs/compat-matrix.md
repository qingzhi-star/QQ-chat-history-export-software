# 兼容性矩阵

每次真机验证一行。**不记录** uin、完整用户路径、昵称、消息样本；只记录
版本、格式形状、cipher 指纹、WAL 状态与检查结论（修正案 A §13.2）。

| 验证日期 | Windows 版本 / 架构 | Python | QQ 版本 | wrapper 形状 | cipher 指纹 | WAL 状态 | 检查结论 |
|---|---|---|---|---|---|---|---|
| 2026-10-28 | Windows 11 专业版 10.0.26200 / x64 | 3.14.6 | 9.9.36-53644 | 1024 字节自定义头 + 4096 页；`size % 4096 == 1024` | `sha512/sha1/48` | `ok`：45 帧，最后 commit 第 45 帧，commit 后 415 页；13/20 个已提交页与主库不同 | **PASS**：doctor 11/11 ok；`quick_check == ok`；`c2c_msg_table` / `group_msg_table` 可 `SELECT COUNT(*)`（86 / 985）；同一快照两次解密 SHA-256 一致；源目录 identity 三元组未变；页 1 SQLite reserved bytes = 80（≠ 密文尾 reserve 48，R34） |

## 备注

- 同日另做一次只读 P1 探测（脱敏）：32 张表；`c2c_msg_table` 86 行、
  `group_msg_table` 991 行；`40800` blob 长度 c2c 中位 103 B / 最大 1522 B，
  group 中位 88 B / 最大 3400 B；解密 1.7 MB 耗时 0.14 s；WAL 61 帧。
  `discuss_msg_table` 等表存在但不在 P0 关键表清单内，留作 P1 输入。
- **fixture 未采集**：`nt_msg_header_page1.bin` 需要人工审查页 1 字符串后
  `git add -f`，当前 fixture 测试 SKIP，属「未验证」。
- **Python 3.12**：本机只有 3.14，按修正案延后到 CI，尚未验证。
- 出现新版本不兼容时，诊断应说「未验证 / 暂不支持该格式」，不得误导为
  「口令错误」。
