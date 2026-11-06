# 绮录 Electron 桌面端

当前正式发行版本 `0.3.0`，已验证平台为 Windows 11 x64。软件全称为「绮录QQ聊天记录导出软件」，品牌及图标继续沿用
[品牌母版与规则](branding.md)。Python 版本唯一来源仍是 pyproject.toml，npm 对应
`0.3.0`；不以普通提交次数递增版本，候选修订递增 rc。

## 运行与文件布局

安装器提供目录选择、桌面快捷方式和卸载程序，可从 Windows 已安装应用卸载。
便携版是 ZIP，完整解压后打开主 exe；无需系统 Python、Node.js 或浏览器扩展。
便携版不注册安装记录，因此没有需要运行的卸载器，移除整个便携目录即可。

```text
绮录QQ聊天记录导出软件.exe
*.dll                         Electron 原生运行库
locales/                      Chromium 语言资源
resources/
  app.asar                    本机 HTML/CSS/JS 与主进程代码
  brand/qilu.png, qilu.ico     既有 D 方案
  core/
    qilu-core.exe             独立本机解析与导出内核
    _internal/                Python 运行库和内核 DLL
```

卸载保留原有 `%LOCALAPPDATA%/QQExport` 账号配置与 DPAPI 口令缓存，不把本人数据放入
安装目录；Electron 配置与原有 QQExport 缓存分开。主进程只通过匿名 stdin/stdout
JSON 管道调用内核，不开放 HTTP 服务或远程端口。聊天文字用 textContent 渲染；
本机资源通过受限协议提供，渲染进程无 Node 权限，开启隔离与沙箱，拒绝外部导航。
配置依据 [Electron 官方安全说明](https://www.electronjs.org/docs/latest/tutorial/security)。

## 功能与滚动

主界面提供自动账号准备及切换、按最新消息排序的实时会话列表、分页聊天预览、
图片系统默认查看器、行内表情、引用卡片、整行勾选、全选/日期范围、多格式导出和搜索。
导出格式卡片的标题、说明和空白区域均可切换。核心导出仍使用原 P1/P2 编排器。
批量队列、增量备份、加密归档、资料库、缓存设置、任务计划等工具全部在
“更多功能”中以实际功能按钮提供，使用 Electron 内置对话框、任务进度和取消/重试。
不再提供跳转经典窗口的操作。搜索支持全库/会话、发送者、类型、日期、精确/正则、
分页、结果导出、可选本机关键词历史和索引清理。

右侧 Fenwick 树维护实际行高，ResizeObserver 在图片/字体变化后同帧重排并保存锚点；
被过滤的空消息高度为零。只保留可视区和缓冲区，页面缓存最多 12 页、可见节点最多
60 个；左栏固定 94 px 行高，仅绘制可见会话。双栏使用 Chromium 原生像素滚动，
不拦截滚轮切成整行，抵达顶/底后保持边界。

## 开发、构建、验证

Windows x64，Python 构建工具链按 requirements-build.lock，Node 开发依赖按
desktop/package-lock.json。用户无需安装这些开发工具。

```powershell
npm.cmd --prefix desktop ci
$env:QILU_PYTHON = '你的 python.exe 绝对路径'
npm.cmd --prefix desktop start
python -m pytest -q -p no:cacheprovider --basetemp build/pytest-electron
npm.cmd --prefix desktop test
npm.cmd --prefix desktop run test:ui
python tools/build_electron.py --wheelhouse build/release-wheelhouse
```

`--electron-dist` 可指定已经校验官方 SHA256 的 Electron 运行库目录，避免重复下载。
构建输出位于 `dist/electron-0.3.0`，包括 Inno Setup 安装器、完整目录 ZIP、
win-unpacked 测试目录和指纹清单。Electron 45 的 `electron_wer.dll` 须与主 exe 同名
（`绮录QQ聊天记录导出软件_wer.dll`），构建时保留该配对。

自动界面测试只用合成的 200 会话/十万条消息，不输出本人聊天内容。
主界面 PDF 使用 Electron 内置 Chromium 的独立隐藏打印窗口，禁止脚本与外部网络，
支持取消且不关闭用户的浏览器。独立 CLI/计划内核保留原 Edge 后备路径。

可通过 QILU_PACKAGED_EXE 指向已解压应用，再运行同一套测试校验冻结后的内核。
生产启动不要设置 QILU_DEMO、QQEXPORT_HOME、QILU_TEST_HOME 等测试变量。

若开发目录继承了不完整的 AppContainer ACL，Electron 可能在启动时退出；在标准用户
目录解压验证可区分目录环境问题与程序问题，不能通过关闭沙箱作为产品修复。
构建用临时目录仅由子进程环境指定，不修改系统 TEMP 或目录访问控制。

安装与卸载由与原项目相同的锁定 Inno Setup 6.7.3 生成；Electron-builder 仅负责
exe/DLL/resources 目录和 ZIP。NSIS 中间卸载器在本机无法可靠写入临时文件，
未作为交付方案，也不将失败的 NSIS 中间产物作为安装包提供。

正式发行在干净提交上运行构建、smoke_electron 和 release_electron；报告绑定清单哈希。
标签工作流只验证源码与 Electron 流程，最终 Release 从本机已验收的两个文件发布，
避免 CI 重建产生未经真机验收的替代资产。详见 release-checklist.md。
