# Phase 3B 学习包 — Chat-first 前端如何接上可信后端

> 本轮起始：`ad52867`（Phase 3A，427 项测试通过）。本轮只在既有架构上补齐前端与两个安全约束，没有新增 Agent 框架、第二个 LLM 验证器或 Word 导出。

## 一、本轮到底改了什么

| 层次 | 之前 | 现在 |
|---|---|---|
| 首页 | `streamlit_app.py` 是「上传→核对→统计」五步工作台，界面上没有对话入口 | 新增 `chat_app.py` + `dashboard/chat.py`，官方启动脚本指向它；旧工作台原样保留 |
| 数据来源 | 「使用示例数据体验」走**上传路径**，导入后被标记 `uploaded_local_only`，外部模型被禁用 | 合成数据走服务端 `/api/datasets/synthetic/preview|commit`，导入后 `source=generated` |
| 查询模式 | `DashboardClient.query()` 只能发 `{question}`，依赖后端自动判断 | 前端显式发 `execution_mode="grounded"`（合成）或 `"local"`（上传），并带 `scope`/`expected_version` |
| 回答 | 后端英文 evidence-token 模板直接展示给工作人员 | 前端从 Evidence 数值确定性生成中文结论；英文原文放进折叠区供核对 |
| 模型工具约束 | 允许模型额外调用参数匹配的 `get_catalog` | 删除该旁路，严格「一问一工具」 |

**为什么旧工作台一行没删**：`test_college_dashboard.py`、`test_workflow_ui.py`、`test_synthetic_panel.py`、`test_dashboard.py` 共 9 处用 `AppTest.from_file("streamlit_app.py")` 并强断言 `session_state.workflow` 和按钮文字。改它的行为会连带破坏 427 项回归。所以新首页用独立入口，用户按 `start_dashboard.ps1` 启动看到的就是 Chat-first —— 这是「不推翻现有前端」与「零弱化测试」同时成立的做法。

## 二、三个最值得理解的函数

### ① `chat.main()` 与 `chat._data_source()` —— 前端状态机与信任边界

**是什么**：`main()` 是页面入口，依次做连接检查、观察数据版本、渲染侧栏数据来源、渲染介绍/示例/对话；`_data_source()` 决定当前允许哪种分析模式。核心只有一句判断：

```python
def _mode(state):
    return "grounded" if state["dataset"].source == "generated" else "local"
```

`state["dataset"]` 只能由两个按钮写入：服务端合成预览+确认，或 Excel 预览+确认。用户无法在页面上直接声明"这是合成数据"。

**为什么需要它**：隐私边界必须在**客户端就决定**，而不是等后端 403 再解释。同时「数据集未确认时禁用输入框」「版本变化清空历史」都由这一个状态对象驱动，避免不同版本的结果混在同一聊天上下文里。

**调用链**：
```
chat_app.py → main() → client.health() / _observe_dataset()
   → _data_source(client, state, info)
        ├─ _synthetic_panel() → synthetic_preview → synthetic_commit → _activate()
        └─ _upload_panel()    → college_preview  → college_commit  → _activate()
   → _examples() → client.catalog() / client.resolve()（过滤出可执行的示例）
   → st.chat_input / 示例按钮 → _ask() → client.query(execution_mode=_mode(state))
```

**如何运行/调试**：
```powershell
.\scripts\start_backend.ps1 -Provider mock
.\scripts\start_dashboard.ps1
```
或纯自动化测试：`.venv/Scripts/python.exe -m pytest -q tests/test_chat_app.py`。调试时在 `st.session_state.chat` 里能直接看到 `messages / dataset / scope / catalog / examples`，全部是页面状态，不含任何成绩明细。

**软件工程知识**：状态机 + fail-closed 默认值（未确认 = 禁用）+ 会话失效（版本变化 = 清空）。**与 Application Engineer 的关系**：企业应用里"当前上下文是什么、能做什么"必须由服务端事实驱动，不能靠 UI 上用户自己勾选；这和 SAP 里基于主数据/权限对象决定可用事务是同一类思维。

---

### ② `chat._evidence_cn()` —— 展示层不重新计算

**是什么**：把 `AgentResponse.evidence[i].data` 里的数值拼成中文句子。例如真实浏览器验收输出：

> 2025–2026学年 · 第1学期 · 课程 工程实践2：共 12 条成绩记录……平均分 74.00 分……及格率 83.33%（10/12），不及格 2 条。成绩分布：0–<60 2条、……

**为什么需要它**：用户要中文回答，但**不能让模型写数字**（会编造），也**不能改后端 narrative**（会破坏 Phase 3A 的 token 校验）。所以把「语言转换」放在展示层：数值全部来自 Evidence，模型原文只留在折叠区。页面还明写一句「以上中文结论由页面依据后端计算证据中的数值生成，不包含模型措辞，也不推断成绩差异的原因」。

**调用链**：`_render_message()` → `_evidence_cn(result, state["catalog"])` → `_statistics_cn()` / `_comparison_cn()`；图表走 `_render_charts()`，同样只读 `data["distribution"]`。`state["catalog"]` 只用于把内部长课程 ID 换成中文课程名。

**如何运行/调试**：`pytest -q tests/test_chat_app.py::test_chinese_course_and_class_questions_show_statistics_and_charts`；或浏览器里点「分析工程实践2的平均分和及格率」，展开「计算证据与技术详情」逐项对账（截图 `answer-with-chart.png`）。

**软件工程知识**：**单一事实来源（single source of truth）** 与「展示与计算分离」。前端是投影，不是第二个统计引擎。**与 Application Engineer 的关系**：企业报表里最容易出事的就是前端二次计算导致口径不一致；正确做法是后端给出带版本的确定性结果，各端只做格式化。

---

### ③ `semantics.validate_intent_call()` —— 本轮的最小安全修复

**是什么**：模型返回的「工具名 + 参数」在执行前与本地已解析的 `ResolvedIntent` 逐字段比对。本轮删掉了这段例外：

```python
# 已删除
if name == "get_catalog" and intent.tool != "get_catalog":
    ...  # 参数匹配就放行
```

现在只剩：
```python
if name == intent.tool:
    if actual == expected: return
raise AgentError("intent_mismatch", "...已阻止执行。", 422)
```

**为什么需要它**：语义层已经在服务端按目录解析完全部课程/班级/学期，`GROUNDED_SYSTEM` 也明令模型「never call get_catalog to look up alternatives」。这个例外只会让模型执行用户没请求的工具，并往回答里塞入额外 Evidence。判断结论：**不必要，且有风险 → 删除**。三条针对性测试覆盖：额外目录调用被拒、端到端 422 且零 Evidence、意图本身就是 `get_catalog` 时不被误伤。

**调用链**：`api.agent_query()` → `resolve_payload()` → `AgentService._run()` → `registry.validate()` → **`validate_intent_call()`** → `registry.execute()` → `AnalyticsEngine`。注意顺序：先结构校验，再意图校验，最后才执行统计。

**如何运行/调试**：`pytest -q tests/test_phase3b_backend.py -k "side_query or extra_get_catalog or catalog_intent"`。手工构造一个 `intent.tool="get_score_summary"` 的 `ResolvedIntent`，再传入 `get_catalog` 的参数，应抛 `AgentError(code="intent_mismatch")`。

**软件工程知识**：**契约校验与信任边界**——「参数合法 ≠ 意图正确」。模型输出一律视为不可信输入，必须在副作用发生前拦截。**与 Application Engineer 的关系**：这是企业里集成外部服务/AI 的标准解法，等价于接口层的输入校验与授权检查，不能靠提示词"请别乱选"来保证。

## 三、典型演示问题（合成数据，Mock 实测）

| 问题 | 工具 | 实测结果 |
|---|---|---|
| 当前数据集的概览是什么？ | `get_dataset_overview` | 12 名学生、2 个班、6 门课、72 条选课记录 |
| 分析工程实践2的平均分和及格率 | `get_score_summary` | 平均分 74.00、及格率 83.33%（10/12）、分布 2/3/3/3/1 |
| 比较 C001 和 C002 的工程实践2成绩 | `compare_classes` | 每班精确均分与及格率，不做原因推断 |
| 查询当前数据集有哪些课程 | `get_catalog` | 目录内课程与班级，均来自真实目录 |
| 比较 2 班和 3 班的成绩 | 无工具 | `needs_clarification`，提示补充课程，零模型调用 |
| 查一下张三的学号和个人成绩 | 无工具 | `unsupported`，提示支持范围，零模型调用 |
| 分析 G1-C99 的平均分 | 无工具 | `needs_clarification`，不允许模型替换成别的课程 |

## 四、隐私与模式矩阵

| 数据来源 | 前端发送 | 后端路径 | 是否外发 | 页面标注 |
|---|---|---|---|---|
| 服务端合成 | `execution_mode="grounded"` | DeepSeek Function Calling + 语义约束 | 仅结构化意图（ASCII `ACADEMIC_INTENT`），无原文 | DeepSeek AI 分析（合成数据）/ 离线 Mock 演示 |
| 上传 XLSX | `execution_mode="local"` | `AgentService._run_local()`，`provider=None` | **零外发** | 本地规则分析（上传数据） |

页面每条回答按 `result.provider` / `result.simulated` 显示**实际**执行方式，因此 Mock 回放不会被冒充成 DeepSeek，local 结果也不会被冒充成 AI 分析。真实上传数据永远走 `local`；新 UI 不提供绕过开关。

## 五、已实现 / 尚未实现

**已实现**：Chat-first 首页；两类数据来源与显式模式；服务端合成激活；Excel 预览/核对/确认；中文确定性回答与图表；可展开证据；目录校验过的示例问题；版本切换清理；中文错误引导；`get_catalog` 旁路修复；29 项新 Mock 测试；真实前后端启动 + 浏览器截图验收。

**尚未实现 / 未验证**：真实 DeepSeek 中文端到端（本轮禁止付费调用）；SSE 流式与 `stages` 实时阶段事件（当前只用普通加载状态，**没有**事后拼接假日志）；完整多轮上下文理解（仅历史展示 + 可选会话范围复用，页面已明确区分）；移动端/无障碍/跨浏览器；Word 报告、多模型、RAG（按架构决策不做）。

**兼容边界**：`configured` 英文旧路径仍走旧的英文词表闸门、不经过语义约束。它只用于保留 `test_agent_service.py` / `test_agent_api.py` 等历史契约，**新 UI 一律发 `grounded`**。下一轮可在迁移这些断言后移除该路径。

## 六、下一轮最值得做的三件事

1. **修 `\b` 边界 bug**：`semantics.py` 用 `\bC(\d{1,3})\b` 提取班级 ID，中文紧邻时（`比较C001和C002的成绩`）因中文字符属于 `\w` 而匹配失败。改成 ASCII 边界 lookaround，并补中英文紧凑写法测试。本轮示例问题已暂用带空格写法规避。
2. **Agent Evaluation**：用授权后的单次真实 DeepSeek 请求 + 评测矩阵（工具选择、参数漂移、歧义、版本变化、隐私），把「能跑」推进到「可度量」。
3. **Debugging / README / 面试准备**：整理这四轮的调用链与取舍，写成简历项目描述与可讲清楚的技术故事。

> 学习建议：先跑 `pytest -q tests/test_chat_app.py`，再在浏览器里按「预览→勾选→确认→点示例」走一遍，最后展开「计算证据与技术详情」把中文结论里的每个数字与 `data` 对上。这条链就是本项目的核心命题：**自然语言 → 正确工具 → 正确参数 → 可信计算 → 结果验证 → 可理解的回答**。
