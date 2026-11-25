# oil-agy-title

Antigravity 桌面端会话自动命名插件（非官方）。

本目录为可安装插件包，包含：
- `hooks.json`：每轮对话结束时非阻塞派生后台命名进程；
- `prompts/naming.md`：按用户近期语言维护 `emoji 对象｜目标` 标题；
- `scripts/`：对话摘录解析、默认 `agy` (`gemini-3.8-flash-low`) 调用、双通道写回与用量统计；
- `skills/oil-agy-title/`：对话内预览、手动重命名、锁定/解锁、暂停/恢复与切换内置模型的管理 Skill。

完整说明、安装方式、兼容范围与数据边界见仓库根目录 [README.md](../README.md)。

Windows：请先按 [Windows 使用说明](../docs/windows.md) 构建适配包，不要直接安装原始 POSIX Hook。Windows 仅预览，不写回标题。
