---
name: oil-agy-title
description: 管理 Antigravity 桌面端会话的后台自动命名：检查运行状态与 Hook、预览或应用会话标题、固定/解锁标题、暂停/恢复自动命名、查看模型用量与切换内置模型。当用户要求管理 Antigravity 桌面端会话标题或配置本插件时使用；不用于文章标题、视频标题或文件重命名，也不在普通对话结束时由主 Agent 主动调用。
license: MIT
compatibility: Antigravity 桌面端专用；需要 Python 3.9+ 与已登录的 agy CLI。macOS 已实测；Linux 仅代码兼容未实测桌面端；Windows 10/11 实验性预览版需先构建 Windows 插件包，尚未真实环境验证；禁止自动写回。
metadata:
  version: "0.1.0"
---

# oil-agy-title

管理 Antigravity 桌面端后台自动命名流程。自动命名由插件根目录的 `Stop` Hook 在后台静默执行；主对话仅响应用户明确提出的检查、预览、固定、暂停或切换内置模型请求。

## 定位入口

执行本 Skill 内的 `scripts/run.py`，它会自动定位插件根目录的后台主程序，不依赖固定安装目录名。下文 `<入口>` 表示该 `scripts/run.py` 的绝对路径。

默认会话 ID 自动读取环境变量 `$ANTIGRAVITY_CONVERSATION_ID`；需要操作其他会话时显式传入 UUID，不猜测 ID。

## 常用操作

- 检查环境、`agy` CLI 与当前会话读取：
  `python3 <入口> doctor`
- 查看配置、已跟踪会话数和最近 5 条后台命名日志：
  `python3 <入口> status`
- 查看独立模型调用用量统计：
  `python3 <入口> usage`
- 预览当前会话新标题（停在候选结果，不写入）：
  `python3 <入口> rename`
- 立即重命名当前会话并写回（仅在用户明确要求改名时使用）：
  `python3 <入口> rename --apply`
- 固定 / 解锁当前会话标题：
  `python3 <入口> lock`
  `python3 <入口> unlock`
- 暂停 / 恢复全局自动命名：
  `python3 <入口> pause`
  `python3 <入口> resume`
- 切换内置命名模型（默认 `gemini-3.8-flash-low`，复用本机 Antigravity 登录，无需额外配置；如需更偏重准确度可换 `gemini-4-argon-low`）：
  `python3 <入口> configure --model gemini-4-argon-low`
  `python3 <入口> configure --model gemini-3.8-flash-low`

## 执行边界与返回状态

- `renamed`：新标题已写回并通过读回校验。`write_backend=conversation_service` 表示通过官方本地服务热更新（侧边栏即时刷新）；`write_backend=pbtxt_fallback` 表示降级写入 `annotations/<id>.pbtxt`（桌面端重载或同步后刷新）。
- `kept` / `unchanged`：当前标题准确、无实质变化或命中纯确认/同轮跳过，保持原样。
- `locked` / `manual_title`：标题受保护（手工改名后自动锁定）。仅在用户明确要求恢复自动命名或覆盖手动标题时，才执行 `unlock` 后再试。
- `stale_result`：模型生成期间对话或标题已变化，自动丢弃旧结果。
- `ambiguous_title`：与同项目已有会话重名且重试后仍无法区分，保留原名。
- `doctor` 通过不代表桌面端 `Stop` Hook 已真实触发；需在新对话结束后通过 `status` 查看日志确认。

## Windows 预览版（实验性）

Windows 使用 `py -3`（或已验证的原生 `python`）替代上述 `python3`。默认 Stop Hook 只记录 `windows_hook_ready`，不调用模型。用户明确同意消耗 Antigravity 额度并发送对话摘录后，可执行 `py -3 <入口> configure --windows-auto-preview`；使用 status 查看 `windows_preview` 候选标题，再通过桌面端手动改名。用 `configure --no-windows-auto-preview` 关闭。显式 rename 仍可预览；禁止 rename --apply、内部 RPC 与 pbtxt 写回。不要宣称已实现 Windows 全自动改名。
