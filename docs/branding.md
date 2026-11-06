# 绮录：名称、图标与界面

用户于 2026-11-06 选定 D「绮录」及其图标，并给出账号卡片/底部操作栏的布局草图。
主窗口、安装显示名、快捷方式、PE 产品名及原生窗口图标使用「绮录」。
内部包名 qqexport、QQExport.exe、发布包文件名、AppId 和已有缓存目录保持兼容，
已有配置与本机加密口令继续沿用。

最终资源：`qqexport/assets/qilu.png`（512×512 RGBA）、`qqexport/assets/qilu.ico`
（16/24/32/48/64/128/256），随 wheel 和冻结包一起分发。
图标由内置 image_gen.imagegen 生成，以用户已选定的原创 D 图为编辑目标；
代码仅做 PNG/ICO 尺寸与格式导出，未重新设计角色。

最终采用的图像编辑提示词：

> Precise alpha cleanup only. Preserve the supplied lilac chibi archivist app icon inside
> its rounded purple square exactly. Outside the single main rounded-square tile, remove
> disconnected white flecks and edge debris, leaving transparent pixels. Preserve the
> character, notebook, eyes, hair clip, colors and square canvas. No additions or text.

头像来自本人本机资料及既有 QQ 公共头像入口；用户名优先读取本机资料中的昵称。
切换列表不扫描所有账号的进程口令，只在明确选择账号后沿用自动取口令流程。
文件共享占用只作为活跃账号的辅助信号，不能单独证明在线登录状态；列表展示本人
本机账号及完整 QQ 号，当前项明确标记，资料缺失时显示「QQ 用户」并保留 QQ 号。
后台资料副本在 profiles 私有临时目录中使用，完成或退出后清理，不进入发布资产。
