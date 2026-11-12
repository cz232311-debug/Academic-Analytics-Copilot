# 项目吃透路线图（面试导向）

> 目标：把 Academic Analytics Copilot 从"AI 帮我写的"变成"我能脱稿讲透、能动手改、能扛住追问"的简历核心项目。
> 用法：按 Phase 顺序推进，每个 Phase 末尾的「过关检查」全部能独立完成才算过。**不要跳读，不要只读不动手。**
> 面向岗位：SAP STAR Application Engineer（成都）——JD 关键词：coding / testing / debugging / logging / structured workflows / LLM integration / AI output validation / evaluation techniques / English communication。

---

## 学习总原则（比内容更重要）

1. **每条知识都要过手**：跑过一次命令 > 读十遍说明。
2. **每个设计都要能答"为什么"**：面试官不问"这是什么"，问"为什么这样"。
3. **每个 bug 都要能讲成故事**：现象 → 假设 → 验证 → 根因 → 修复 → 教训（STAR 结构）。
4. **诚实是加分项不是减分项**：能讲清楚"哪些是 Mock、哪些是真实模型验证过"比含糊其辞强十倍。项目本身的 answer_mode 诚实标注就是证据。

---

## Phase 0：把系统亲手跑起来（半天）

**知识点**：Python 虚拟环境、依赖管理、双进程架构（FastAPI 8000 + Streamlit 8501）、loopback 无部署。

**动手清单**：
- [ ] 读 `START_HERE.zh-CN.md`，不看笔记独立启动后端与前端
- [ ] 激活合成数据（预览 → 勾选 → 确认），问三个问题：数据集概览 / 某课程成绩 / 两个班对比
- [ ] 上传一个自己的 XLSX，观察 answer_mode 标注变成"本地规则"
- [ ] 跑一次全量测试：`pytest`，确认 611 passed，记录耗时
- [ ] 跑一次 semantic 审计：`python scripts/phase3d_semantic_audit.py`，看 76/76

**过关检查**：不看任何文档，从克隆仓库到跑通第一个问答，15 分钟内完成。

---

## Phase 1：一条请求的完整旅程（1-2 天，最重要）

**这是面试的主动脉。目标：画出"用户输入一句中文 → 页面显示中文结论"的完整链路图，每个环节能说清输入输出。**

以「工程实践2 这两个班谁及格率更高」为例，逐跳追踪：

| 跳 | 位置 | 干什么 | 必须能答的问题 |
|---|---|---|---|
| 1 | `dashboard/chat.py::_ask()` | 前端收集问题，POST `/api/agent/query` | 前端为什么不直接调模型？（信任边界：浏览器不可信） |
| 2 | `api.py::agent_query()` | 校验请求、决定 grounded / local 路径 | 上传数据为什么绝不触达模型？（隐私边界，`provider=None`） |
| 3 | `agent/semantics.py::resolve_question()` | 正则 + 目录绑定，产出 `ResolvedIntent` | 为什么是规则解析而不是让 LLM 理解中文？（确定性、可测试、零成本；歧义要澄清而不是猜） |
| 4 | `agent/service.py::_run()` | 构造 `ACADEMIC_INTENT` 计划发给模型 | `GROUNDED_SYSTEM` 为什么要求"exactly that tool with exactly those arguments"？（模型无参数自由） |
| 5 | `agent/tools.py` + `analytics.py` | 工具执行，产出 `Evidence`（数字全部来自这里） | 数字为什么不可信模型生成？（零容错场景，模型会幻觉） |
| 6 | `agent/analysis.py` + claim catalog | 模型只能从预生成 claim 里选 `claim_ids` 排序 | claim catalog 协议解决了什么问题？（模型有组织权、无事实权） |
| 7 | `render_analysis()` | 服务端拼中文句子，填数字槽 | 如果模型选了不存在的 claim_id 会怎样？（回退 deterministic_fallback） |
| 8 | `dashboard/chat.py::_mode_label()` | 首行标注真实 DeepSeek / Mock / 本地 / 澄清 / 回退 | 为什么要在 UI 上暴露实现方式？（诚实 > 面子，且这是面试卖点） |

**动手清单**：
- [ ] 打开后端日志，发一个问题，对照上表逐跳找到日志证据
- [ ] 用「有没有人不及格」触发一次澄清（clarification），走一遍 choices 分支
- [ ] 上传数据后再问同样问题，确认 `model_turns=0`
- [ ] 用白纸默画这张链路图，标注每跳的文件名

**过关检查**：被人随机问上表任意一个"必须能答的问题"，30 秒内答出且能指到代码位置。

---

## Phase 2：核心设计决策（1 天，面试"为什么"题库的弹药）

**知识点地图**：

1. **Bounded autonomy（受限自治）**：LLM 有真实职责（选证据、排序、定风格）但零信任（数字、事实、工具参数全部后端控制）。能讲清这个权衡：零容错场景值得牺牲模型的自由发挥；代价是洞察上限受 `analysis.py` 派生丰富度限制；演进方向是"模型提假设→后端验证"。
2. **信任边界三件套**：数据源二选一互斥（`source=generated` vs `source=uploaded_local_only`）、上传数据零外发、付费调用授权闸（`--i-authorize-paid-calls`）。
3. **诚实标注体系**：`answer_mode` 六种状态；`MockDemoProvider` 不返回 analysis 字段所以 Mock 必走回退——能解释这个"故意的"设计。
4. **语义守卫**：`_unresolved_course_reference()` 防静默全数据集统计；`_UNSUPPORTED` / `_INJECTION` 拒绝名单/排名/预测/注入。
5. **契约与模型**：`Evidence / ToolTrace / ModelTrace / AgentResponse`（`agent/models.py`）、Pydantic v2 校验、`digest()` SHA-256 溯源。

**过关检查**：能脱稿回答"你这个系统里 AI 到底做了什么、没做什么、为什么"——这是本场面试最重要的一道题，建议把答案写出来并背熟中英文两个版本。

---

## Phase 3：测试体系（1 天，JD 硬指标 testing）

**知识点**：pytest、fixture、参数化、字节稳定 fixture、离线 stub（零网络测试）、集成测试 vs 单元测试。

**动手清单**：
- [ ] 读 `pyproject.toml` 的 pytest 配置，理解 `testpaths` / `pythonpath`
- [ ] 挑 3 个测试文件精读：`tests/test_analytics.py`（纯逻辑）、`tests/test_chat_app.py`（前端状态机）、`tests/test_phase3d_ui_status.py`（stub provider 技巧）
- [ ] 理解 `ad6dde2` 那个 commit：XLSX 内嵌时间戳导致 flaky，怎么修成 byte-stable——这是现成的 debugging 故事
- [ ] **改坏再修好**（关键练习）：把 `analytics.py` 的及格率分母故意改错 → 跑测试看哪些红了 → 读失败信息 → 修回。这个"从红到绿"的体感就是 testing/debugging 能力本身
- [ ] 给 `semantics.py` 的一个句型自己写一个新测试用例，跑过

**过关检查**：能回答"611 个测试都测什么层次？为什么不依赖真实网络？flaky 测试怎么修？"

---

## Phase 4：排障故事库（半天，debugging 面试题直接来源）

每个故事按 现象 → 假设 → 验证 → 根因 → 修复 默讲到顺口：

1. **假 404**：HTTP keep-alive 第二请求 404 → 怀疑后端路由 → 实际根因是 shell 的 `HTTP_PROXY=127.0.0.1:48191` 环境变量 → `httpx` 设 `trust_env=False`。教训：先怀疑环境再怀疑代码。
2. **旧进程跑旧代码**：验收结果异常 → 查 PID 73284 启动时间 03:27 早于提交时间 → 重启后正常。教训：验证前先确认"跑的是不是这份代码"；结果文件如实改名 `*-stale.json`。
3. **C2 首次 502**：真实 DeepSeek 调用 502（1446ms 无 usage 未计费）→ 单次重试成功 → 如实记录而非掩盖。教训：可观测性（日志带 status）让故障自解释。
4. **flaky fixture**：XLSX 时间戳 → byte-stable 修复。
5. **语义误判**：「有没有人不及格」被当成课程名 → 不是打补丁而是系统性剥离疑问词 → 76 用例全量审计验证。教训：修一类问题而不是修一个 case。

**过关检查**：每个故事 2 分钟讲完，且能说"我从中养成了什么习惯"。

---

## Phase 5：AI 评估体系（半天，命中 JD 的 evaluation 关键词）

**知识点**：LLM 输出验证、评测基线、A/B 对照方法论、协议验收。

**动手清单**：
- [ ] 读 `docs/development/phase3d-review/` 四份文档：PHASE3D_REVIEW / PROTOCOL_ACCEPTANCE / AB_COMPARISON / AGENT_EVAL_BASELINE
- [ ] 能说清五维 A/B 对照是哪五维（信息点/统计值数/局限说明/无据归因/模型轮次），为什么不比字数
- [ ] 能说清 20 条 eval 基线的 `meta.warning` 为什么存在（Mock 成功率 ≠ 真实准确率）
- [ ] 看一眼 `scripts/phase3d_agent_eval.py` 的结构，知道怎么加一条新用例

**过关检查**：能回答"你怎么评估一个 LLM 功能的好坏？"——用本项目作为答案。

---

## Phase 6：模拟面试问答（持续，中英双语）

**必问题（中文，每题写 150 字答案并脱稿）**：
1. 介绍一个你最复杂的项目 → 一句话定位 + bounded autonomy 叙事
2. 项目里最难的技术问题 → 语义误判系统性修复 或 假 404
3. 你怎么保证 AI 输出的质量 → claim catalog + 评测基线 + answer_mode
4. 你写过多少测试？怎么组织的 → 611 个、分层、离线 stub、flaky 修复
5. 如果让你继续改进，下一步做什么 → 受控规划（模型提假设→后端验证）+ 多轮上下文
6. 这个项目的局限是什么 → 规则式中文理解的天花板、洞察上限、无部署无认证（如实讲）

**英文准备**：3 分钟项目陈述稿（problem → design → evidence → tradeoff），以及 JD 关键词的英文表达：prompt engineering、AI output validation、evaluation baseline、structured logging、regression test suite。

---

## 节奏建议

| 日 | 内容 |
|---|---|
| Day 1 | Phase 0 + Phase 1 前 4 跳 |
| Day 2 | Phase 1 后 4 跳 + 默画链路图 |
| Day 3 | Phase 2 + Phase 3 |
| Day 4 | Phase 4 + Phase 5 |
| Day 5 起 | Phase 6 循环 + 补 GitHub/CI/英文 README |

完成 Phase 0-3 后，你才算是"这个项目的主要贡献者"；完成 Phase 4-6 后，才算是"能打赢面试的候选人"。
