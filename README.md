<p align="center">
  <img src="./assets/readme/hero.webp" width="100%" alt="oil-agy-title，Antigravity 桌面端自动命名插件：让侧边栏会话标题跟上讨论内容，跟随用户语言、保护手动标题、后台自动运行。插画：戴眼镜的线条小人把分类标签贴到会话卡片上，暖黄色牧羊犬叼着备用标签在旁边帮忙。">
</p>

`oil-agy-title` 是一个 Antigravity 桌面端插件，打包了 `Stop` Hook 和配套管理 Skill。每轮对话结束后，它在后台用独立的轻量模型检查最近几轮讨论，自动维护「类别 emoji + 对象｜目标」格式的会话标题；主题没变就保持原样，命名过程不进入原对话，也无需额外配置 API Key。

> 这是个人做的非官方插件，和 Google 没有关系。命名规范与并发保护设计参考了 [oil-codex-title](https://github.com/oil-oil/oil-codex-title)。

<p align="center">
  <img src="./assets/readme/before-after.svg" width="100%" alt="侧边栏前后对比：默认首轮生成的英文或笼统标题，整理为带类别 emoji 与全角分隔符的对象｜目标结构，并自动跟随用户的中文或英文交流语言。">
</p>

## 为什么做这个插件

Antigravity 桌面端默认只在第一轮自动起一次标题，而且经常起成英文短语；聊着聊着换了重点、修了具体模块，或者开着十几个并行会话时，侧边栏很难一眼分清哪条在做什么。

`oil-agy-title` 把标题维护从对话里拆出去：

- **一眼区分对象和目标**：统一用 `emoji 对象｜目标`（恰好一个全角 `｜`），把具体模块、工具或主题放在最前面。
- **跟着用户的语言走**：依据最近几轮**用户自己表达需求**的主要语言决定标题语言，不因为旧英文标题、助手回复、代码片段或日志把标题带偏；偶尔夹杂一句外语或技术名词不会来回切换，持续换用另一种语言后自动迁移。
- **不覆写手动标题**：建立稳定基线后，一旦检测到你在桌面端手动改了标题，自动锁定该会话，不再改动。
- **克制消耗**：纯确认回复（「好的」「继续」「ok」）最多连续免模型跳过两轮；后台子任务中途回报不重复触发；子 Agent 与已归档会话直接跳过。

## 类别与命名格式

标题按持续工作的产物归类，不随「修复」「提交」「测试」等临时动作频繁换图标：

| 类别 | emoji | 适用场景 | 示例 |
| --- | :---: | --- | --- |
| 工具开发 | 🧩 | 功能开发、缺陷修复、插件、Skill 与自动化脚本 | `🧩 邮箱验证码｜过期排查` / `🧩 Email verification｜Fix expiry` |
| 内容制作 | 🎬 | 视频选题、脚本、拍摄、剪辑与发布 | `🎬 图像模型对比｜视频策划` |
| 对比调研 | 🔎 | 模型对比、方案评估、资料与岗位调研 | `🔎 向量数据库｜检索方案对比` |
| 页面设计 | 🎨 | 界面布局、视觉稿、动效与样式还原 | `🎨 登录表单｜布局优化` |
| 方法整理 | 📝 | 规范文档、操作指南、文章与演示文稿 | `📝 镜头语言｜制作方法整理` |
| 日程安排 | 📅 | 带明确时间节点的日程、提醒与待办 | `📅 周一例会｜议程整理` |
| 环境配置 | ⚙️ | 安装、权限、账号与本地运行环境配置 | `⚙️ 本地代理｜端口与证书配置` |
| 一般讨论 | 💬 | 有明确对象但不属于以上类别的讨论 | `💬 团队分工｜边界讨论` |

## 工作原理

<p align="center">
  <img src="./assets/readme/how-it-works.svg" width="100%" alt="后台命名流程：每轮结束触发 Stop Hook，约 60 毫秒内派生后台子进程并返回空 JSON；后台读取 transcript.jsonl 最近 5 轮有效对话，调用独立轻量模型判断是否改名，优先走 ConversationService 热更新，缺失环境变量时原子写入 annotations/*.pbtxt。">
</p>

1. **非阻塞触发**：Antigravity 的 `Stop` Hook 目前为同步执行，插件入口会在约 60ms 内派生脱离会话的后台 worker 并立即输出 `{}` 返回，不阻塞交互，同时通过环境变量与桌面端路径双重防止递归触发。
2. **双通道写回**：
   - 当运行环境注入了本地服务变量（`ANTIGRAVITY_LS_ADDRESS`、`ANTIGRAVITY_CSRF_TOKEN`，例如在对话中通过 Skill 手动触发改名）时，优先调用 Antigravity 官方本地接口 `product.v1.ConversationService/UpdateConversation` 实时热更新侧边栏、SQLite 与全文检索索引。
   - 当后台 `Stop` Hook 环境未注入本地服务变量时，自动降级为原子更新 `~/.gemini/antigravity/annotations/<id>.pbtxt`（桌面端重载或刷新会话状态后同步到列表与索引）。

## 安装

依赖：Python 3.9+、Antigravity 桌面端以及已登录的 `agy` CLI。无需额外配置或 API Key。

> 本插件包含根目录的 `hooks.json` 与 `plugin.json`，不能用仅安装单个 Skill 的 `npx skills add`（会漏掉 `Stop` Hook）；请按下面任一方式安装完整插件包。

### 方式一：让 Antigravity 帮你安装

直接把下面这句话发给 Antigravity：

```text
请帮我安装这个 Antigravity 完整插件（包含 Stop Hook，不要只安装 Skill）：https://github.com/oil-oil/oil-agy-title ，把仓库里的 plugin 目录装到全局插件目录后跑一次 doctor 检查。
```

### 方式二：命令行安装

```bash
git clone https://github.com/oil-oil/oil-agy-title.git
```

```bash
agy plugin install oil-agy-title/plugin
```

没有用 `agy plugin install` 的话，也可以直接把 `plugin` 目录复制进全局插件目录：

```bash
cp -R oil-agy-title/plugin ~/.gemini/config/plugins/oil-agy-title
```

装好后无需手动修改 `config.json`，新开一条桌面端会话聊一轮即可生效。桌面端的 Customizations / Plugins 面板里也能直接开关 `Oil AGY Title`。

## 日常怎么用

装好后直接对 Antigravity 说即可（由内置 Skill 调用脚本）：

- 「检查 oil-agy-title 的运行状态和 Hook」
- 「预览当前对话的新标题」 / 「给当前对话重新命名」
- 「固定当前对话的标题」 / 「解锁当前对话标题」
- 「暂停自动命名」 / 「恢复自动命名」
- 「查看自动命名模型用量」
- 「把命名模型换成 `gemini-4-argon-low`」或切回 `gemini-3.8-flash-low`

也可以直接在终端运行 Skill 内的入口脚本（路径随实际安装位置变化）：

```bash
RUN="$HOME/.gemini/config/plugins/oil-agy-title/skills/oil-agy-title/scripts/run.py"

python3 "$RUN" doctor              # 检查环境、agy CLI 与当前会话读取
python3 "$RUN" status              # 查看配置、已跟踪会话数与最近 5 条日志
python3 "$RUN" usage               # 汇总独立命名模型调用次数与 token 用量
python3 "$RUN" rename <会话ID>      # 仅预览，不写入
python3 "$RUN" rename <会话ID> --apply
python3 "$RUN" lock <会话ID>        # 固定标题，阻止后续自动改名
python3 "$RUN" unlock <会话ID>
python3 "$RUN" pause               # 全局暂停
python3 "$RUN" resume              # 全局恢复
```

## 默认模型说明

默认使用 **`agy` 内置的 `gemini-3.8-flash-low`**，直接复用现有 Antigravity 账号登录，消耗账号内模型额度，无需配置任何外部密钥。在 macOS 实测中，单回合耗时约 **11 秒**，`thinking_tokens=0`，提示词缓存命中率约 80%，在内置模型里速度最快且准确度稳定；如果更在意复杂任务的提炼准确度、能接受约 20 秒延迟，可切换到 `gemini-4-argon-low`。

## 测试与排查

运行仓库单元测试（仅用 Python 标准库，不调用真实模型、不触碰真实会话）：

```bash
python3 -m unittest discover -s plugin/tests -v
```

排查后台是否触发时，依次查看：

1. 运行 `python3 "$RUN" doctor`，确认 `agy_ok: true`、`hooks_file_exists: true`。
2. 在新会话完成一轮对话后运行 `python3 "$RUN" status`，查看 `recent_events` 中的状态（`renamed` / `kept` / `locked` / `error`）与写回通道。
3. 精简审计日志与用量账本位于 `~/.gemini/antigravity/plugin_data/oil-agy-title/` 下的 `logs/` 和 `usage/`。

## 数据、兼容范围与局限

- **数据去向与隐私**：命名时仅提取最近 5 轮用户需求与最终回复摘录，通过本机已登录的 `agy` CLI 发送至 Antigravity 模型服务，不向任何第三方服务发送数据；本地数据目录仅记录状态、候选标题、简短理由与 token 统计，不保存完整对话原文。
- **后台生效时机**：实测当前 Antigravity 桌面版执行 `Stop` Hook 时未向 Hook 进程注入 `ANTIGRAVITY_LS_ADDRESS`，后台自动触发走 `.pbtxt` 原子写入兜底；桌面端重载或刷新会话状态后会同步到侧边栏与索引。在对话内通过 Skill 执行 `rename --apply` 时走官方 `UpdateConversation` 接口，可立即热刷新。
- **历史会话不批量改动**：只对安装后产生新对话轮次的会话生效，不会主动扫描和重命名旧历史。
- **兼容范围**：仅适用于 **Antigravity 桌面端**。macOS（Python 3.9 与 3.14）已通过单元测试与真实会话验证；Linux 仅代码兼容、桌面端未实测；Windows 10/11 新增实验性预览包（尚未真实环境验证），不支持自动写回；详见 [Windows 使用说明](docs/windows.md)。离线环境可查看状态和暂停，生成新标题需要联网。

## 许可证

[MIT](LICENSE)

## Windows 10/11 实验性预览版

请阅读 [Windows 使用说明](docs/windows.md)，先构建适配包再验证。直接安装原始 `plugin/` 仍使用 POSIX Hook，Windows 不适用。默认不自动调用模型，不调用内部 RPC、不写 Antigravity 会话文件。启用后台预览后仅生成候选，需用户手动应用。
