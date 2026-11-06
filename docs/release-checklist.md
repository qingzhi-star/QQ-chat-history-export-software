# 发布验收清单

1. 每个任务已独立提交并推送，工作区干净；普通测试和两种隐私巡检通过。
2. 仅修改 `pyproject.toml` 版本并 `--sync-version`，标签为 `v<完全相同版本>`。
3. 使用固定工具链全新构建；保留 build-manifest.json 和 SHA256SUMS。
4. 对该构建运行 `tools/smoke_release.py`；报告绑定构建清单哈希，旧报告不能复用。
5. 运行 `tools/release_gate.py dist/release --smoke build/smoke-report.json --tag v<版本>`。
6. 正式版还须提供同提交的 windows10、windows11、gui_regression、real_pdf、
   performance、source_readonly 六项 PASS 证据；通过 `--evidence` 或仓库变量
   RELEASE_EVIDENCE_JSON 提供。格式为 `{"source_commit":"<sha>","checks":{...}}`。
   每个值必须为字符串 PASS，未验证/SKIP/历史证据均不接受。
7. 标签工作流通过测试、固定构建、实际冒烟和发布门后，上传同版本 EXE 与 ZIP。
   不手动上传源码，不覆盖已有标签/资产；rcN 必须标记 prerelease。

当前自动化已经覆盖 Windows 11 本机的合成启动、中文/空格路径、缺失运行库诊断、
隔离身份安装与卸载、原子替换故障及隐私巡检。Windows 10 真机、完整 GUI 交互、
真实 Edge PDF、性能及源文件只读性的本轮正式版证据须单独记录；缺少时只允许候选版。

Inno 编译器下载来自[官方 6.7.3 发布](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3)，
安装包和编译器均校验固定哈希。静默测试遵循[官方命令行参数](https://jrsoftware.org/ishelp/topic_setupcmdline.htm)。
