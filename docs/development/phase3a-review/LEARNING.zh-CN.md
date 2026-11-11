# Phase 3A 学习包 — 业务语义层与 DeepSeek Function Calling 正式集成

日期：2026-11-11 · 分支：`feature/chat-first-agent` · 起始提交：`63315a0`（398 项测试全绿）

本轮只做一件事：**把已经写好但零调用点的语义验证代码，接进既有的模型调用链**。没有新建 Agent 框架、没有第二个 LLM 验证器、没有改动四个工具、没有改 Streamlit UI。

---

## 一、修改了什么 / 为什么 / 如何验证

| 文件 | 改动 | 为什么 | 验证 |
|---|---|---|---|
| `src/copilot/agent/models.py` | `QueryRequest.execution_mode` 增加 `grounded` | 让「语义约束路径」成为显式、可测试的请求契约，而不是靠隐式猜测 | 29 项新测试 |
| `src/copilot/api.py` | `agent_query` 把 `resolve_payload` 产出的 `intent` 真正传给 `AgentService`；非 ASCII 问题自动按语义约束处理；澄清/不支持时**不构造 provider**；`/api/agent/resolve` 的 `generated_only` 改为 `!= "local"` | 中文必须先本地编译成结构化意图才能出境；上传数据在读任何模型配置之前即 403；澄清提示不该被「没配 DeepSeek Key」遮蔽 | 403/409/200 notice 测试 + 出站 payload 检查 |
| `src/copilot/agent/service.py` | `query()` 增加 `intent` 参数；新增 `_run_notice()`；`_run()` 增加 `intent` 分支（`ACADEMIC_INTENT` + `GROUNDED_SYSTEM`）；工具执行前调用 `validate_intent_call()` | 同一个 Function Calling 循环里，把「模型收到什么」和「允许执行什么」都锁在已验证意图上 | 传输边界、C01→C02、错误工具、Evidence 一致性测试 |
| `tests/test_agent_semantic_integration.py`（新增） | 29 项 Mock 回归测试 | 覆盖中文解析、参数偏离、歧义、不存在实体、版本变化、隐私边界 | 全量 427 passed |
| `docs/development/phase3a-review/*`、`02b-workbench.md` | 本轮记录 | 项目规则要求每轮留可复核记录 | — |

**关键取舍**：`tests/test_agent_service.py` 直接单参数调用 `query()` 并断言模型 user content 是规范化英文问题；`test_agent_api.py` 用 `"Average score for G1-C99"` 断言 404、用 `"Average score"` 断言 502/503/504。若把语义约束强行套到所有 configured 请求，这些既有行为都会变。因此 **`intent=None` 时完整保留原英文循环**，`grounded` / 中文请求才启用约束——两走的是**同一个 `_run()` 函数**，不是第二条流水线。

---

## 二、完整调用链（合成数据 + 中文请求）

```
POST /api/agent/query  {"question":"分析 G1-C01 的平均分和及格率"}
  │
  ├─ api.agent_query()
  │    ├─ resolve_payload(payload, generated_only=True)
  │    │    ├─ store.overview(generated_only=True)      ← 隐私闸门：uploaded → 403
  │    │    ├─ store.reporting_catalog()                ← 真实目录（课程/班级/学期/专业）
  │    │    ├─ resolve_question(question, catalog, scope) → ResolvedIntent
  │    │    │      status=ready, tool=get_score_summary,
  │    │    │      arguments={term, course_id, class_id, grade_level},
  │    │    │      canonical_question="ACADEMIC_INTENT {...}"   ← 纯 ASCII，不含用户原文
  │    │    └─ 版本/来源二次核对 → (intent, dataset_version)
  │    ├─ grounded = execution_mode=="grounded" or not question.isascii()
  │    ├─ intent.status=="ready" → agent_runtime() 取 provider；否则 provider=None 直接返回 notice
  │    └─ AgentService(...).query(question, intent=intent, ...)
  │
  ├─ AgentService.query()
  │    └─ _run(..., intent=intent)          ← 与英文路径同一个循环
  │         ├─ messages = [GROUNDED_SYSTEM, intent.canonical_question]
  │         ├─ provider.complete()          ← DeepSeek 只看到结构化意图
  │         └─ 每个 tool_call：
  │              ├─ registry.validate()     ← Pydantic 结构校验
  │              ├─ validate_intent_call()  ← 语义一致性校验（新增断点）
  │              ├─ registry.execute(generated_only=True) → Evidence
  │              └─ available_tokens() → 模型写叙述 → render_narrative()（token 填充，模型写不出数字）
  │
  └─ AgentResponse(status, answer, evidence, tool_calls, resolved_scope, dataset_version)
```

**上传数据路径**：`execution_mode="local"` → `provider=None` → `_run_local()` → `resolve_question()` → 一个受控工具 → `verified_localized`。全程无外部请求，`provider="local"`，不会被标成 DeepSeek。

---

## 三、三个最值得理解的核心函数

### ① `api.agent_query()` — 请求分派与「隐私决策前置」

1. **是什么**：`/api/agent/query` 的 FastAPI 路由处理函数，决定一次请求走哪条执行路径、是否可以构造 LLM provider。
2. **为什么需要它**：隐私判断必须发生在「读 API Key / 发起外发请求」之前。如果先构造 provider 再检查数据来源，一个缺失的 Key 会先抛 503，把「这份数据不允许外发」这个真实原因掩盖掉；更糟的是配置齐全时可能先把数据发出去。
3. **怎样被调用**：FastAPI 收到 POST → Pydantic 校验 `QueryRequest` → 本函数 → `AgentService.query()`。
4. **如何运行/调试**：
   ```bash
   # 只看解析结果、不触发模型（Mock 环境）
   .venv/Scripts/python.exe -m pytest -q tests/test_agent_semantic_integration.py::test_stale_expected_version_is_rejected_before_the_model
   # 出站请求体里有什么
   .venv/Scripts/python.exe -m pytest -q tests/test_agent_semantic_integration.py::test_chinese_question_sends_only_verified_intent_to_provider
   ```
   第二个测试用 `httpx.MockTransport` 捕获真实 HTTP body，断言其中没有「分析 / 平均分 / 及格率 / student_id」。
5. **软件工程知识**：**Fail-closed 授权边界**、**最小数据出境（data minimization）**、依赖注入（`agent_provider`）让测试可以替换外部系统。
6. **与 Application Engineer 的关联**：企业应用里「谁能把什么数据送到哪个外部系统」必须在服务端边界强制，不能靠前端隐藏或提示词约定。SAP 场景中的客户主数据、薪酬、成本中心同理——这类判断一旦放错位置，就是合规事故而不是 UI bug。

### ② `AgentService._run(..., intent=None)` — 单循环、双模式

1. **是什么**：唯一的 Function Calling 主循环，负责组装 messages、驱动模型、执行工具、汇总 Evidence、渲染回答。
2. **为什么需要它**：本轮最容易写错的地方是「为了支持中文再复制一份循环」。两份循环会各自演化，日后修一个 bug 漏另一个。加一个 `intent` 参数让两个模式共享预算控制、异常包装、Evidence 校验和叙述验证。
3. **关键分支**：
   - `intent is None`：`canonical_question(question, catalog)` + 原 `SYSTEM`（英文受限词表）。**完全保持旧行为**。
   - `intent` 提供：`intent.canonical_question`（`ACADEMIC_INTENT {...}`）+ `GROUNDED_SYSTEM`（明确要求模型精确回放计划，不许换课程/班级/学期），并在 `registry.validate()` 之后、`registry.execute()` 之前插入 `validate_intent_call()`。
   - `intent.dataset_version != info.dataset_version` → 409 `dataset_changed`，防止用旧目录解析出的 ID 查新数据。
4. **如何运行/调试**：`tests/test_agent_service.py` 里的 `ScriptedProvider` 会保存每次 `complete(messages, tools)` 的深拷贝；在测试里打印 `provider.requests[0]` 就能看到模型实际收到的 system/user/tool 消息。`event("tool_call", ...)` 会写入 `copilot.agent` logger，可用 `caplog` 观察。
5. **软件工程知识**：**开闭原则**（扩展参数而非复制实现）、**契约校验与信任边界**、**幂等的版本快照（optimistic concurrency）**。
6. **与 Application Engineer 的关联**：企业集成中同一个业务流程常要服务多个渠道（UI、批处理、API），把差异收敛成参数而不是分支副本，是降低长期维护成本的核心手法；`expected_version` 这种乐观锁在并发写入的 ERP 系统里是标准做法。

### ③ `semantics.validate_intent_call(intent, name, args)` — 「参数合法 ≠ 意图正确」

1. **是什么**：一个纯本地、无 I/O 的校验函数。把模型返回的工具名 + Pydantic 参数，与 `resolve_question()` 已经确认的 `intent.tool` / `intent.arguments` 逐字段比对；不一致抛 `AgentError("intent_mismatch", 422)`。
2. **为什么需要它**：这正是你反复强调的那条原则。模型给出格式完全合法的 `G1-C02` 时，`registry.validate()` 会通过、统计引擎会算出一份漂亮的数字——但用户问的是 `G1-C01`。**没有这个检查，系统会把一个语义错误包装成 `completed` 的成功分析。**
3. **怎样被调用**：`resolve_question()` → `ResolvedIntent` → `api.agent_query()` → `AgentService.query(intent=...)` → `_run()` 的工具循环 → `validate_intent_call()` → `ToolRegistry.execute()`。本轮之前它零调用点；现在它是 ready 路径的必经关卡。抛出的 `AgentError` 继承 `DataError`，所以 `_run()` 原有的 `except` 会保留 `intent_mismatch` 这个具体错误码并写成 `status="error"` 的 `ToolTrace`，而不是被包装成 500。
4. **如何运行/调试**：
   ```bash
   .venv/Scripts/python.exe -m pytest -q tests/test_agent_semantic_integration.py -k drift
   .venv/Scripts/python.exe -m pytest -q tests/test_agent_semantic_integration.py::test_explicit_course_in_question_is_never_silently_replaced
   ```
   前者用脚本化模型故意返回 `G1-C02`，断言 422 + 零 Evidence + `tool_calls[0].error_code == "intent_mismatch"`。
5. **软件工程知识**：**契约式设计（Design by Contract）**、**服务端不信任任何外部输出**、**结构化错误码而非模糊失败**、双层防御（prompt 软约束 + 服务端硬约束）。
6. **与 Application Engineer 的关联**：任何「AI 选参数 → 后端执行」的系统都需要这道关。换成企业术语，就是「用户输入的业务对象主键不能被模型改写」——订单号、成本中心、公司代码被悄悄换掉，比程序崩溃严重得多。

---

## 四、隐私边界矩阵（本轮实测）

| 场景 | 路径 | 外部模型 | 结果 |
|---|---|---|---|
| 合成数据 + 中文统计 | `grounded`（自动） | 只收到 ASCII `ACADEMIC_INTENT {...}` | 200 completed，Evidence 可追溯 |
| 合成数据 + 中文含课程名/专业/年级/学期 | `grounded` | 只收到目录 ID | 200，参数由目录唯一确定 |
| 合成数据 + 中文歧义/不存在实体/敏感信息 | `grounded` notice | **零调用、零 API Key** | 200 `needs_clarification` / `unsupported`，中文提示 |
| 上传数据 + configured / grounded | `resolve_payload(generated_only=True)` | **零调用** | 403 `agent_privacy_block` |
| 上传数据 + `local` | `_run_local()` | **零调用** | 200 `verified_localized`，`provider="local"`、`model_turns=0` |
| 数据版本/来源变化 | 版本守卫 | 零调用 | 409 `dataset_changed` |
| DeepSeek 返回错误 | 异常链 | 无兜底假数据 | 502 + 明确错误码，回答里没有统计数字 |

---

## 五、可复现的演示问题（Mock 环境）

```
分析 G1-C01 的平均分和及格率              → get_score_summary / 73.33 / 66.67%
比较 2024 级软件工程 1 班与 2 班的高等数学 (I)-1 成绩  → compare_classes / C001,C002
2024 级软件工程的成绩总体情况怎么样？      → get_score_summary / grade_level=2024 / major=software_engineering
查询当前数据集有哪些课程                  → get_catalog
数据概览                                  → get_dataset_overview
分析 G1-C99 的平均分                      → needs_clarification（中文提示，零工具调用）
比较 2 班和 3 班的成绩                    → needs_clarification（要求补充课程/班级）
查一下张三的学号和个人成绩                → unsupported（仅支持聚合统计）
```

---

## 六、真实测试结果

```
pytest -q --tb=short --basetemp=.test-tmp-p3a1  tests/test_agent_service.py tests/test_agent_api.py \
         tests/test_agent_local_mode.py tests/test_deepseek_narrative.py \
         tests/test_synthetic_activation.py tests/test_dashboard_http.py
    → 1 failed, 83 passed（发现 /api/agent/status 新增字段破坏 DashboardClient 严格模型，已撤销该字段）
pytest -q tests/test_dashboard_http.py::test_http_dashboard_client_gets_actual_seeded_dataset → 1 passed
pytest -q tests/test_agent_semantic_integration.py                    → 29 passed, 5.75s
pytest -q --tb=short --basetemp=.test-tmp-p3a-final                  → 427 passed, 143.84s, 1 warning, 0 failures
```

全程 Mock（`tests/conftest.py` autouse 强制 `COPILOT_LLM_PROVIDER=mock` 并清除 Key）。**没有发起任何付费 DeepSeek 请求**；`OpenAICompatibleProvider` 只用 `httpx.MockTransport` 模拟 HTTP 传输。

唯一 warning 是既有的 Starlette TestClient 弃用提示。**本轮未启动 Uvicorn / Streamlit，也未做浏览器功能检查**——HTTP 层测试不等于浏览器验收。

---

## 七、仍未解决 / 下一步

1. **真实 DeepSeek 中文端到端未验证**：需单独授权且会产生费用，本轮按你的要求禁止。所有语言理解证明都来自本地语义层 + Mock 回放，模型侧的中文表现尚未实测。
2. **`configured` 英文路径仍走旧英文 gate**：为保留既有 398 项断言而留的兼容契约，未受语义约束。下一阶段 Chat-first 前端应统一发 `execution_mode="grounded"`。
3. **回答仍是英文 token 模板**：`narrative.py` 用 evidence token 填充以保证「模型写不出数字」，目前句子是英文；中文叙述本地化未做。
4. **`stages` 字段仍为空**：`ExecutionStage` 已定义但没有埋点，也没有 SSE 流式；本轮不做事后拼接冒充实时进度。
5. **`validate_intent_call` 保留 `get_catalog` 旁路**：若模型先查目录且参数与已确认范围一致，既有设计允许执行；`GROUNDED_SYSTEM` 明确禁止模型这么做，但硬约束未收紧。
6. **语义层是受限学业语法，不是通用 PII 检测器**：隐私保证主要来自「只把结构化意图发给模型」这一点，而非正则能穷举所有敏感信息。
7. **Chat-first 前端尚未开发**（本轮按要求不做 UI）。

下一步最值得做的：`client.py` 增加 `resolve()` / `grounded` 参数 → 对话视图 → 示例数据改接 `/api/datasets/synthetic/*`；随后才进入真实功能验收、Debugging、Agent Evaluation 与 README/简历准备。
