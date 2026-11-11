# 项目交接单（跨对话对齐）

> **给新对话的一段话**：把下面这段直接粘到新对话开头，就能让新会话立即接上本项目的进度，不必重读全部历史。

```
继续 D:\Document\ChatGPT\SAP-ApplicationEngineer 这个项目（分支 feature/chat-first-agent）。
请先读 HANDOFF.zh-CN.md、START_HERE.zh-CN.md、docs/development/phase3b-review/FILES.md，
再执行 git log --oneline -15 和 git status --short 对齐当前代码状态，
然后告诉我你理解的当前界面、已验证结论和未完成项，等我确认后再动手。
不要修改 main 分支，不要发起付费模型调用，不要读取或打印任何 API key。
```

## 为什么需要这份文档

AI 对话的上下文不共享。**唯一可靠的进度载体是仓库本身**：代码 + 测试 + 提交历史 + 已提交的文档。
`.workbuddy/memory/` 只是本工作区的补充日志，不进 Git、换机器就丢失；聊天记录检索也只是回忆，不是事实源。
所以每次实质改动都做独立提交，并把「当前状态」压成这份文档，随代码一起提交。

## 对齐进度的三个层次

| 层次 | 载体 | 用途 | 是否随仓库走 |
|---|---|---|---|
| 事实源 | Git 提交 + 代码 + 测试 | 精确还原任意时刻的状态 | 是 |
| 当前状态快照 | `HANDOFF.zh-CN.md`（本文件） | 新对话第一条消息就能定位 | 是 |
| 本地补充 | `.workbuddy/memory/YYYY-MM-DD.md`、`MEMORY.md` | 决策理由、踩坑、用户偏好 | 否（仅本机） |

新对话标准动作：`git status --short` → `git log --oneline -15` → 读本文件 → 读 `START_HERE.zh-CN.md`（操作）→ 读 `docs/development/phase3b-review/FILES.md`（验收证据）。若需要回溯某次决策的原因，再查对应的提交说明或本地日志。

## 当前状态（2026-11-12）

- 分支 `feature/chat-first-agent`；`main` 未改动。工作区只保留三项**故意不提交**的内容：owner-owned 离线回执 `docs/development/results/14-deepseek-offline-demo.json`、`.workbuddy/`、`docs/development/phase3b-review.zip`。
- 当前界面是 **Chat-first**（`chat_app.py` + `src/copilot/dashboard/chat.py`）。旧的五步工作台 `streamlit_app.py` 一行未改，仍被既有 AppTest 回归直接启动。
- 两种数据 provenance 严格分开：服务端生成的合成数据 → `execution_mode="grounded"`（DeepSeek）；上传的 XLSX → `execution_mode="local"`（纯本地规则，绝不外发）。中文结论由页面依据后端 Evidence 数值确定性渲染，模型不能写数字。
- 全量回归 **461 passed / 77.77s**（唯一 warning 是既存 Starlette TestClient 弃用提示）。不要并行跑两个 pytest 进程：测试夹具曾因 XLSX 内嵌时间戳跨秒不稳定，已修，但仍建议独占运行。
- 最新一轮按用户四条反馈改完：**① 删除「会话范围」控件**，每题独立解析；**② 已确认的上传数据集重开页面自动沿用**（合成数据不沿用，避免意外付费）；**③ 上传预览改为「完整问题清单 + 原表行号/列字母定位的单元格核对」**，问题单元格优先、上限 200 条并提示省略数，不再有 `学生0001` 这类重新编号别名；**④ 用户对话气泡靠右**（`row-reverse` + `margin-left:auto`）。真实浏览器已验收：气泡右侧间距 0、左侧留白 163px，助手仍在左。
- 验收截图：`review-issues-with-coordinates.png`、`review-auto-restored.png`、`review-user-bubble-right.png`（均在 `docs/development/phase3b-review/`）。细节见 `FILES.md` 第 8 节。

## 硬约束（改代码前必读）

1. **隐私**：预览响应、日志、清洗回执、分享包都不得包含姓名、原始学号或未识别单元格原文。用户上传的成绩单只做本地分析，`external_llm_allowed=false` 不可被调用方改写。定位一律用「原表行号 + 列坐标」，不要用重新编号的别名冒充脱敏。
2. **显式确认**：上传/合成数据必须「预览 → 勾选 → 确认」才激活；只有已确认的上传数据集会在重开页面时自动沿用（合成数据不自动沿用，因为那是唯一可能触发付费调用的路径）。
3. **证据可核验**：任何中文统计结论都必须能追到后端 Evidence 的数值；页面不推断原因、不编造数字。
4. **零意外付费**：未经用户明确授权，不发起真实 DeepSeek 调用；后端用 `COPILOT_LLM_PROVIDER=mock` 验证。不读取、不打印、不存储 API key。
5. **启动脚本**：`scripts/start_backend.ps1`（`-Provider mock` / `-ForcePrompt` / 端口占用预检）、`scripts/check_deepseek_key.ps1`（免费余额接口自检）、`scripts/start_dashboard.ps1`。提交说明里避免终端名与命令动词，曾触发安全策略误判。
6. 用户的 8000/8501 服务属于用户自己的会话；验收请用独立端口（如 8010/8511）+ 独立数据库 + Mock，结束后按端口精确停止。

## 已知边界与未完成

- `semantics.resolve_question()` 的 `\bC(\d{1,3})\b` 在中文紧贴 `C001` 时边界不匹配（中文属 `\w`）；示例问题用带空格写法规避，待改 ASCII 边界并补回归。
- 授权后仍需一次真实 DeepSeek 端到端 + Agent Evaluation 矩阵（工具选择、参数漂移、歧义、版本、隐私）。
- 未覆盖：`stages`/SSE、多轮上下文、移动端与无障碍、跨浏览器、真实模型推理的浏览器验收。
- 明细 ZIP 下载目前只在旧工作台，Chat-first 页面没有。

## 关键文档索引

| 文件 | 内容 |
|---|---|
| `START_HERE.zh-CN.md` | 安装、启动、数据来源、提问、错误排查、隐私边界、旧工作台附录 |
| `docs/development/phase3b-review/FILES.md` | 交付清单、验证结果、截图、未覆盖范围 |
| `docs/development/phase3b-review/LEARNING.zh-CN.md` | 本轮工程决策与学习记录 |
| `docs/development/02b1-workflow.md` | 原始工作流与导入/档案隐私设计 |
| `docs/development/02b-workbench.md` | 各 Phase 进度索引 |
| `README.md` | 项目定位、启动方式、能力说明 |
