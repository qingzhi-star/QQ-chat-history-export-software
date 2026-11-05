# 性能基准（P1+ T06）

**测量日期：** 2026-11-06  
**环境：** Windows 11 x64、Python 3.14.6、SSD、4 核 CPU/16GB 内存级别  
**脚本：** `tools/bench_export.py`  
**数据：** 合成文本消息（每条约 50 字符），不含图片/文件/表情，用于比较回归，
不代表真实含媒体库的绝对时间。

## 运行方式

```powershell
python tools/bench_export.py --messages 1000 --formats txt
python tools/bench_export.py --messages 10000 --formats txt
python tools/bench_export.py --messages 10000 --formats txt,html
```

输出字段：`seconds`（导出总耗时）、`peak_mb`（tracemalloc 峰值）、
`messages_ok`、`warnings`。

## 结果

| 场景 | 消息数 | 格式 | 耗时 | 峰值内存 | 目标 |
|---|---:|---|---:|---:|---|
| 基线 | 1,000 | TXT | 0.139 s | 1.3 MB | — |
| 回归 | 10,000 | TXT | 1.113 s | 9.9 MB | ≤ 3 s |
| 回归 | 10,000 | TXT + HTML | 1.601 s | 12.5 MB | 参考 |

> 1,000 条首次测量为 5.507 s。定位到 `iter_chat_rows` 每行重复执行
> `PRAGMA table_info`（1,000 条约 11,700 次查询），加列缓存并移出循环后
> 降至 0.139 s。该修复同时改善 GUI 与会话列表读取。

## 未覆盖 / 待测

- 带图片的 PDF：受 Edge 打印与图片解码影响，需单独基线与媒体样本。
- 10 万条消息、2 万张图片的 GUI 滚动与内存：P2 虚拟化后测量。
- 冷启动、首屏、切换会话、多选响应：需 GUI 级采样（P1+ P01–P05）。
- 真实 QQ 库的 WAL/解密开销：需脱敏真机数据，不在本基准内。

## 结论

P1+ P07（10,000 条 TXT ≤ 3 s）已达成；列缓存修复是当前最高收益优化。
其余性能指标仍需按任务书分别测量，不能以本表替代 GUI/PDF 结论。
