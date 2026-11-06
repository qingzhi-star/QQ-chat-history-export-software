# 发布验收清单

## Electron 0.3.0 正式发行

本轮按用户要求发布正式版，已验证支持范围为 **Windows 11 x64**。
Windows 10、其他架构及目标四核机不冒充 PASS，也不列入本轮已验证范围。
旧 Tk 发布清单保留在下方，不能把旧 Tk 安装包当作 Electron 正式版上传。

1. `pyproject.toml` 是版本来源；npm 映射、PE 版本、README、标签和资产名一致。
2. 干净提交上运行完整测试、隐私检查、Python 3.12/3.13/3.14 和 Electron CI。
3. `tools/build_electron.py` 使用锁定 Python/Node 依赖和 Inno Setup，输出新目录，
   记录同提交源码指纹、依赖哈希、完整 exe/DLL/resources 目录和两个分发资产。
4. `tools/smoke_electron.py` 在 Windows 11 对该产物验证中文/空格路径、真实安装、
   启动、卸载、无系统 Python/Node、内置工具、加密归档、搜索及内置 PDF（含图片）。
   合成源数据库、附件和归档前后字节不变；报告绑定当前 electron-build.json 哈希。
5. `tools/release_electron.py` 校验同提交、同构建、所有检查为 True；正式版还需
   Windows 11 x64 平台证据与双栏十万条消息/200 会话滚动 p95 ≤1000/55 ms。
   这是本机合成采样，不扩展成所有设备帧率、冷图片或旧性能表的预算保证。
6. 推送 `v0.3.0` 标签并等待标签工作流通过。该工作流验证 Electron 源码和内置 PDF，
   不自动重建并替换已经过 Windows 11 验收的本机资产。
7. 先创建 GitHub Release 草稿，仅上传同版本安装器和便携 ZIP，核对远端 SHA-256、
   标签与提交后发布。正式版不带 prerelease，标记 Latest；从不覆盖已有标签或资产。
   清单/校验值放发布说明，源码包由 GitHub 自动提供。

## 历史 Tk 发布门（0.1 / 0.2）

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

P2 已有 Windows 11 合成 GUI/RSS、真实 Edge PDF/取消和源库字节不变证据，详见
[P2-performance.md](P2-performance.md)。55FPS 与冷缩略图 200ms 尚未达标，目标
4 核机和 Windows 10 未验证，不能填写 performance/windows10 为 PASS。
当前准备的 0.2.0rc1 只跑候选门；最终提交构建与冒烟报告保留本机，不创建稳定标签。

Inno 编译器下载来自[官方 6.7.3 发布](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3)，
安装包和编译器均校验固定哈希。静默测试遵循[官方命令行参数](https://jrsoftware.org/ishelp/topic_setupcmdline.htm)。
