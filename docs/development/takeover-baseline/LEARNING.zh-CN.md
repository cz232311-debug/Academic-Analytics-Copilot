# 接管与基线修复 · 学习记录（2026-11-11）

本轮是**项目接管后的第一个开发轮**，范围严格限定为：保护 Codex 留下的未提交在制品、建立可回滚的检查点、修复 8 个失败测试、恢复稳定基线。本轮**没有**开始 Chat-first 前端改造，没有重写统计引擎，没有删除报告模块。

## 1. 起始快照与在制品处理

- 工作区：`D:/Document/ChatGPT/SAP-ApplicationEngineer`（原目录，未复制、未新建项目）。
- 起始分支与提交：`main` @ `d7c010a`（`docs: record synthetic data validation and operator workflow`）。
- 接管时的工作区：8 个已修改文件、3 个未跟踪新文件，共 11 个在制品；另有本轮生成的临时 pytest 日志。
- 实测基线：`.venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=.test-tmp-takeover` → **8 failed, 387 passed, 1 warning, 63.94s**。
- 新建分支 `feature/chat-first-agent`，10 个在制品进入 WIP 检查点提交 `31834ad`，提交信息明确标注 "unverified" 并写明接口断裂，**没有声称这些文件是在原始编辑时刻提交的**。
- 未纳入 Git、仍保留在工作区：`docs/development/results/14-deepseek-offline-demo.json`（离线 Mock 演示重新运行后只变 request_id 与耗时，属于运行时产物，且属于 owner-owned 记录）。本轮的 pytest 日志移入已被忽略的 `.test-tmp-takeover/` 目录保留，未删除、未提交。
- 11 个文件均做过凭据模式检查，无 API Key、真实学生记录或密钥；`.gitignore` 已排除 `data/*.sqlite3*`、`*.xlsx`、`.env`、`.import-history/` 与 `.test-tmp*/`。

## 2. 8 个失败的根因

`api.py:188` 以四个参数调用 `AgentService.query(question, scope=..., expected_version=..., execution_mode=...)`，而 `service.py:106` 的 `query()` 只接受 `question`。FastAPI 端点因此抛 `TypeError`，被包装成 500。失败集中在 `test_agent_api.py`(6)、`test_dashboard_http.py`(1)、`test_deepseek_narrative.py`(1)；其中 `test_dashboard_http` 的一条是下游客户端把 500 翻译成了通用错误。

**这不是测试写错了，是 WIP 的 API 层与 Service 层没有同步。** 修复方式是补全 Service 层契约，而不是删测试、跳测试或削弱断言。

## 3. 修复方案与取舍

关键约束来自 `tests/test_agent_service.py`：它直接以单参数调用 `AgentService.query()`，并断言发给模型的 user content 必须是规范化英文问题（`"what is the average score and pass rate for g1-c01"`），还断言中文问题会触发 `question_privacy_block` 且 provider 零请求。因此：

- **默认 configured 路径完全保持原有英文 Function Calling 循环**，只增加可选参数：用 `expected_version` 做版本守卫，把 `scope` 回写到 `resolved_scope`。
- `execution_mode="local"` 或 `provider is None` 时走**新的本地确定性分支**：不构造、不调用任何模型，直接解析意图 → 执行一个受控工具 → 用真实 Evidence 渲染回答。
- `api.py` 的 configured 分支改为**总是使用配置 provider**。`resolve_payload` 保留为目录/版本/隐私守卫（uploaded 数据在读 provider 配置之前即 403），但它返回的 `ResolvedIntent` 暂不驱动模型调用——这是下一个增量的接线工作。

取舍说明：如果本轮就把 `ACADEMIC_INTENT` 计划注入模型消息或启用 `validate_intent_call`，会改变"未知课程返回 404 `unknown_entity`"等既有断言，等于把基线修复和功能开发混在一起。按小步增量原则，这两件事分开做。

## 4. 三个核心代码讲解

### 4.1 `AgentService.query()`（`src/copilot/agent/service.py`）

1. **是什么**：Agent 的公开入口，现在签名为 `query(question, *, scope=None, expected_version=None, execution_mode="configured")`。
2. **为什么需要它**：API 层已经按新契约传四个参数；不补全就会 `TypeError`。同时它是"用户请求 → 可信回答"的唯一边界，超时、`AgentError`、`DataError` 都在这里统一转成带 `request_id` 的安全错误。
3. **怎样被调用**：`api.agent_query()` → `AgentService(store, provider, timeout_seconds=...).query(...)`；内部按 `provider`/`execution_mode` 分派到 `_run()` 或 `_run_local()`。测试里 `tests/test_agent_service.py` 也直接单参数调用它。
4. **如何运行/调试**：`pytest tests/test_agent_api.py tests/test_agent_service.py -q`；临时加 `caplog.set_level(logging.INFO, logger="copilot.agent")` 可看 `query_started` / `model_turn` / `tool_call` 事件。
5. **软件工程知识**：**向后兼容的接口演进**（新增可选关键字参数而不破坏旧调用方）、**统一异常边界**、**超时预算**。
6. **与 Application Engineer 的关联**：企业系统里接口契约变更必须同时覆盖所有调用方；能定位"调用方已改、被调方未改"这类跨层不一致，并做兼容修复，是应用工程师最常见的日常。

### 4.2 `AgentService._run_local()`（`src/copilot/agent/service.py`）

1. **是什么**：provider 为空时执行的确定性路径：`store.overview(generated_only=False)` → `store.reporting_catalog()` → `resolve_question()` → `ToolRegistry.validate/execute` → `render_fallback()`。
2. **为什么需要它**：真实上传数据禁止发往外部 DeepSeek，但仍应能在本地回答聚合统计问题。`generated_only=False` 在这里是刻意的安全选择，因为整个链路不产生任何外部请求。
3. **怎样被调用**：`POST /api/agent/query` 带 `execution_mode:"local"` → `api.py` 传 `provider=None` → `query()` 分派到 `_run_local()`。
4. **如何运行/调试**：`pytest tests/test_agent_local_mode.py -q`；三个测试分别验证 uploaded 数据可本地回答、含糊问题不产生证据、configured 模式仍拒绝 uploaded 数据。
5. **软件工程知识**：**信任边界与最小权限**、**fail-closed 隐私设计**、**用已有组件组合新路径而非重写计算逻辑**。
6. **与 Application Engineer 的关联**：SAP 类企业应用经常需要在"数据不能出内网"的前提下保留分析能力；按数据来源分派执行路径、而不是在提示词里请求模型"别看敏感数据"，是可靠的设计。

### 4.3 `api.agent_query()`（`src/copilot/api.py`）

1. **是什么**：`POST /api/agent/query` 的路由处理函数，按 `execution_mode` 分派两条路径。
2. **为什么需要它**：隐私判断必须发生在读取模型配置之前——否则"没配 Key"会掩盖"这份数据根本不该外发"的决定。
3. **怎样被调用**：Streamlit `DashboardClient.query()` 或任意 HTTP 客户端；configured 路径先过 `resolve_payload(generated_only=True)`，再 `agent_runtime()`，最后 `AgentService`。
4. **如何运行/调试**：`pytest tests/test_agent_api.py -q`，重点看 `test_upload_source_is_blocked_before_any_provider_call` 与 `test_configuration_failure_does_not_break_deterministic_apis`；真实服务启动后用 Swagger 复现。
5. **软件工程知识**：**前置守卫（guard clause）**、**配置与授权分离**、**HTTP 状态码语义**（403 隐私、503 配置、504 超时、404 未知实体、422 参数）。
6. **与 Application Engineer 的关联**：企业 API 的错误分类直接决定运维能否快速定位问题；把"配置问题"和"数据权限问题"混在一起是最常见的可维护性缺陷。

## 5. 实现 / 已验证 / 待办（严格区分）

**已实现**：接口契约补全；local 确定性路径；`resolved_scope` 回写；configured 路径的隐私与版本守卫；三个新测试。

**已实际验证（Mock，无付费 LLM 调用）**：
- `pytest tests/test_agent_api.py tests/test_agent_service.py tests/test_deepseek_narrative.py tests/test_synthetic_activation.py -q` → 70 passed, 8.81s。
- `pytest tests/test_agent_local_mode.py -q` → 3 passed, 1.95s（首轮发现 `common` 字典与 `evidence=[result]` 重复关键字导致 500，已修复后复验通过）。
- `pytest -q --tb=short --basetemp=.test-tmp-final` → **398 passed, 1 warning, 64.18s**。唯一 warning 是既有的 Starlette TestClient 弃用提示。

**待办（本轮明确未做）**：`validate_intent_call()` 仍未接入执行链（"问 C01 却选 C02"的拦截尚未生效）；`stages` / `verified_localized` 之外的本地中文叙述尚未本地化；`/api/agent/resolve` 尚无前端调用；Chat-first 对话界面未开发；真实 DeepSeek 端到端演示未执行（需单独授权）；完整 Agent Evaluation 未开始。

## 6. 问题与取舍记录

1. WIP 文件在修复前必须先建立可回滚检查点，否则一旦修坏无法区分"Codex 原来写的"和"本轮改的"。这比直接在工作区改更慢，但可追溯。
2. 离线演示 receipt 属于重新运行产生的产物，虽是被跟踪文件也不纳入提交；它保留在工作区，交由 owner 决定。
3. 首个 local 测试失败暴露了字典解包与显式关键字重复的问题（`TypeError` 被顶层 `except Exception` 包装成 500）。这提醒我们：宽泛的异常兜底会掩盖编码错误，调试时要看 `copilot.agent` 日志事件顺序。
4. 本轮**不**导出 cumulative diff / per-commit patch 包：审查产物不能递归包含自己的最终提交，完整的累积差异留到下一个功能增量完成、快照边界固定后统一产出。

## 7. 可独立分享的范围

可分享：本文件、`FILES.md`、`src/copilot/agent/service.py`、`src/copilot/api.py`、`tests/test_agent_local_mode.py`、以及 `docs/development/` 下的设计与记录文档。

不可分享：`data/*.sqlite3*`、`data/.import-history/`、任何上传的原始 XLSX、`.env`、`docs/` 下未经人工核验的界面截图、私有运行日志。所有测试证据均为合成数据与脱敏聚合结果。
