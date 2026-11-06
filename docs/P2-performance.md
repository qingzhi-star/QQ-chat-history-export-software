# P2 性能验收记录

更新：下方原始表格是 0.2.0rc1 的历史数据，不能直接作为恢复旧 UI 的 rc2 指标。
rc2 直接复用 0.1.0 的 Text/多选原函数，在不改视觉的前提下缓存行高；工作区复测为
十万条 / 两万图首屏 346.30ms、首张缩略图 346.31ms、45.15FPS、更新 p95 42.80ms、
最大 89.00ms、RSS 峰值 115.60MiB、3 个消息页 / 18 个可视行。源库 SHA256 未变。
这些是同一合成规格的单轮测量，保留 P01/P02/P09 预算例外；原版外观按用户要求优先，
后续性能优化应在该外观内进行，不得再通过换皮改变 UI。原始报告为本机
build/classic-ui-source-performance.json。未把历史更高的 FPS 冒充 rc2 成绩。

2026-11-06，Windows 11、Python 3.14.6，24 核 / 24 线程、约 31.4GiB 内存。
这是 T10 工作区合成验收，不冒充目标 4 核 / 16GiB 机器或最终正式版同提交证据。
没有读取真实 QQ 数据或进程内存。原始数字保存在本机 build/p2-*.json，可用下列命令
复现；GUI 测试会生成并清理 2 万个合成 PNG，准备阶段不计入打开会话耗时。

| 指标 | 预算 | 实测 | 结论与口径 |
|---|---|---|---|
| P01 首屏 | ≤300ms | 284.54ms | 十万条，点击后可见行全部可读；单轮 |
| P02 滚动 | ≥55FPS | 52.24FPS | **未达**；240 次单行滚动，主线程更新 p95 24.71ms，最大 30.18ms |
| P03 内存 | ≤500MB | 进程 RSS 峰值 114.49MiB | 十万条 / 两万张 320×240 合成 PNG；Windows PSAPI 全进程峰值 |
| P04 会话搜索 | ≤200ms | 32.85ms | 十万条，热查询；首次建索引 2.418s |
| P05 全库搜索 | ≤500ms | p95 225.85ms | 五十万条 / 五十会话，五次热查询；首次索引 24.697s，峰值 RSS 265.41MiB |
| P06 打开会话 | ≤800ms | 定位索引 238.07ms + 首屏 284.54ms | 不含 QQ 解密和合成数据准备 |
| P07 批量 | 主线程阻塞 ≤100ms | 最大 tick 间隔 12.00ms | 五十会话 / 共一千条 TXT，10ms 心跳；全部成功 |
| P08 增量 | 100 条 ≤5s | 627.02ms | 十万条纯文本基线追加 100 条 TXT/HTML |
| P09 缩略图 | ≤200ms | 284.55ms | **冷启动未达**；从点击到可见缩略图，与首屏同时就绪 |

滚动数据是 Tk 更新循环的吞吐与时间采样，不是 DWM/GPU 展示帧率。缓存页 3 个、
行控件池 14 个；GUI 峰值不含搜索索引，图片规模不代表 2 万张超高像素照片。
第一次未优化为 24.64FPS；加入消息行状态复用与按需几何重算后为 52.24FPS。
首屏、FPS 会随主机负载波动，不把单轮数字解释为所有设备保证。

预算例外与后续：

- P02 仍有新行 Tk 布局与主线程 ImageTk 构造成本；已消除重复字体/正文配置与无变化
  布局。后续 T12 在目标 4 核机器进行 5 轮采样，再考虑 Canvas 原生文字/图片行，
  保持当前选择、引用、图片交互正确性，不为追数字隐藏图片。
- P09 的冷加载与首屏布局共享预算；后续 T12 区分空缓存/热缓存、真实大图解码，
  研究先绘制文字再调度缩略图的优先级。当前后台加载可取消资料切换，内存和磁盘有界。
- 首次搜索建索引超出热查询预算，默认按需建立内存索引以避免无需求时复制全部正文。
  界面后台执行并提供取消/清除；后续可评估显式持久索引，但本版不默认落明文索引。
- Windows 10、目标中配主机、全量真实账号媒体及计划任务系统触发仍未验证；因此不能
  移除 rc 标记。历史 P1 实机结论保留原日期，不充作本轮正式验收。

真实 Edge 本地图片 PDF 产出 40913 字节，取消专属 Edge 工作进程通过。
合成数据库操作前后 SHA256 一致。普通回归覆盖中文路径、无外链、选择/日期、搜索
跳转、批量失败/取消/恢复、增量冲突、归档校验/加密和资料库回滚。

复现：

```powershell
python tools/bench_acceptance.py gui --report build/p2-gui.json
python tools/bench_product.py --messages 100000 --search
python tools/bench_acceptance.py search --report build/p2-search.json
python tools/bench_acceptance.py batch --report build/p2-batch.json
python tools/bench_acceptance.py incremental --report build/p2-incremental.json
python tools/bench_acceptance.py pdf --report build/p2-pdf.json
```

远端 T09 提交的 Python 3.12 / 3.13 / 3.14 全通过：
[Actions 42147204106](https://github.com/qingzhi-star/QQ-chat-history-export-software/actions/runs/42147204106)。
发布候选版另在最终提交上重新确认 CI 与冻结程序冒烟。
