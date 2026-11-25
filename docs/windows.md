# Windows 10/11 实验性预览版

当前是 Windows 移植候选，不是已验证的正式版本。原作者 MIT 许可不变。未找到 Google 公开支持的后台指定桌面会话改名接口，所以 Windows 禁止内部 ConversationService RPC、pbtxt 写回以及 SQLite 写入；`rename --apply` 会报错。SQLite 和 transcript 只读解析仍依赖 Antigravity 本地格式，格式变化可能导致预览失效。

## 1. 构建及离线检查（PowerShell）

在仓库根目录运行：

```powershell
py -3 --version
agy --version
py -3 -m unittest discover -s plugin/tests -v
py -3 tools/prepare_windows.py --output .\dist\windows\plugin
agy plugin validate .\dist\windows\plugin --strict
```

需要 Python 3.9+、原生 agy.exe 已加入 PATH、已安装 Antigravity。若没有 py 启动器但有原生 python.exe，则将上述 py -3 替换为 python，并在构建命令追加 `--launcher python`。构建不安装、不调用模型；目标已存在时拒绝覆盖，另选新目录即可。官方 Windows Hook 由 cmd.exe 执行，建议使用支持引号修复的 agy 1.5.8 或更新版本；需实测桌面端实际 Hook 行为。

## 2. 安装及验证（仅在离线检查通过且用户同意后）

```powershell
agy plugin install .\dist\windows\plugin
$Run = Join-Path $HOME '.gemini\config\plugins\oil-agy-title\skills\oil-agy-title\scripts\run.py'
py -3 $Run doctor
py -3 $Run status
```

若安装路径不同，使用 agy plugin list --json 确认实际路径。默认 Stop Hook 在符合桌面会话条件时仅记录 windows_hook_ready，不发送模型请求。先新建无敏感内容的测试会话，确认 status 中出现该状态。doctor 通过不等于真实 Hook 已触发。

## 3. 可选标题预览

显式 `py -3 $Run rename <会话UUID>` 预览当前会话标题；也可在桌面端通过 Skill 请求预览。如果确认接受对话摘录发送至 Antigravity 模型服务及额度消耗，才开启后台预览：

```powershell
py -3 $Run configure --windows-auto-preview
py -3 $Run status
py -3 $Run configure --no-windows-auto-preview
```

后台候选位于 status 的 recent_events（windows_preview），用户自行在桌面端改名。每个用户轮次最多发起一次预览流程（原有模型格式或重名重试仍可能产生额外调用）；检测到标题变化会保守锁定，可显式 unlock。Windows 使用官方 headless stream-json stdin 输入，避免对话内容出现在命令行。该方式不是 ACP；如仅接受 ACP，保持自动预览关闭且不执行 rename。模型及命令参数仍需在本机版本验证。未确认服务条款和账号风险，不保证零风险。

## 4. 停用和卸载

```powershell
py -3 $Run pause
agy plugin disable oil-agy-title
agy plugin uninstall oil-agy-title
```

不自动删除本地状态和审计日志；位于 ~/.gemini/antigravity/plugin_data/oil-agy-title。不要删除真实会话数据。

## 5. 验收边界

先确认离线测试、构建包、官方插件校验、中文/空格路径和桌面 Stop Hook。再由用户决定是否在测试会话调用一次模型，核验 stream-json 输出、无递归、进程退出、额度及候选标题。不得把本分支当作已经实现自动标题写回的 Windows 正式版。

官方参考：[Hooks](https://antigravity.google/docs/hooks)、[插件](https://antigravity.google/docs/build-plugins)、[Headless](https://antigravity.google/docs/cli/headless/)、[CLI reference](https://antigravity.google/docs/cli/reference)。
