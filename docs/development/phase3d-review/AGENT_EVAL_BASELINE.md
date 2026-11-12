# Phase 3D — Agent Evaluation 基线（20 条，离线夹具）

> **这不是模型准确率。** 全部 20 条的「模型回复」都是进程内 scripted fixture，
> `provider_name = "scripted_mock"`、`is_real_deepseek_run = false`、`live_model_calls = 0`。
> 本基线度量的是**本地语义解析、工具/参数正确性、协议校验、确定性渲染与数值一致性**。
> 真实 DeepSeek 的协议遵循度、claim 选择质量、延迟与成本**尚未测量**。
> 脚本：`scripts/phase3d_agent_eval.py`；结果：`results/agent-eval-baseline.json`。

## 1. 五个记录维度

| 维度 | 记录内容 | 判定依据 |
|---|---|---|
| 意图解析 | `status` / `tool` / `arguments` / 澄清消息 | `resolve_question()` 的输出与每条用例的期望 |
| 工具正确性 | 工具名是否等于期望、参数是否完整 | `ResolvedIntent.arguments` + `validate_intent_call()` |
| 事实一致性 | 版本对齐、Evidence ID 有效、回答中的小数可追溯 | `AgentResponse.evidence` / `dataset_version` / 回答文本 |
| 综合分析质量 | `analysis_status`、claim IDs、四段是否齐全、局限与建议是否存在 | `VerifiedAnalysis.sections` |
| 失败原因 | 非 ready 的澄清/拒绝原因、协议违规的 `analysis_fallback_reason` | `intent.message` / `analysis_fallback_reason` |

## 2. 20 条用例与结果

| ID | 类别 | 问题 | 意图状态 | 工具 | 分析状态 | 结果 |
|---|---|---|---|---|---|---|
| E01 | 预设统计问题 | 当前数据集的概览是什么？ | ready | get_dataset_overview | verified | PASS |
| E02 | 预设统计问题 | 查询当前数据集有哪些课程 | ready | get_catalog | verified | PASS |
| E03 | 预设统计问题 | 分析工程实践2的平均分和及格率 | ready | get_score_summary | verified | PASS |
| E04 | 预设统计问题 | 查询软件工程有哪些课程 | ready | get_catalog | verified | PASS |
| E05 | 中文自然表达变体 | 看一下工程实践2的成绩 | ready | get_score_summary | verified | PASS |
| E06 | 中文自然表达变体 | 帮我看看工程实践2的均分 | ready | get_score_summary | verified | PASS |
| E07 | 中文自然表达变体 | 工程实践2有没有人不及格 | **needs_clarification** | — | — | **FAIL** |
| E08 | 模糊课程名称 | 分析体育课的成绩 | needs_clarification | — | — | PASS |
| E09 | 模糊课程名称 | 分析体育类课程的成绩 | needs_clarification | — | — | PASS |
| E10 | 明确课程成绩分布 | 分析体育 III的成绩分布 | ready | get_score_summary | verified | PASS |
| E11 | 明确课程成绩分布 | 工程实践2的分数都集中在哪一段 | ready | get_score_summary | verified | PASS |
| E12 | 班级比较 | 比较 C001 和 C002 的工程实践2成绩 | ready | compare_classes | verified | PASS |
| E13 | 班级比较 | 比较2024软工1班和2024软工2班的工程实践2成绩 | ready | compare_classes | verified | PASS |
| E14 | 综合分析请求 | 分析工程实践2的成绩表现，有哪些值得关注的地方？ | ready | get_score_summary | verified | PASS |
| E15 | 综合分析请求 | 工程实践2的成绩有什么特点 | ready | get_score_summary | verified | PASS |
| E16 | 超出范围的问题 | 预测某位同学下学期会不会挂科 | unsupported | — | — | PASS |
| E17 | 超出范围的问题 | 列出所有学生的姓名和学号 | unsupported | — | — | PASS |
| E18 | 模型输出不符合协议 | 分析工程实践2的成绩分布（注入自由文本） | ready | get_score_summary | **fallback** | PASS |
| E19 | 模型输出不符合协议 | 分析工程实践2的成绩分布（注入不存在的比较 claim） | ready | get_score_summary | **fallback** | PASS |
| E20 | 数值和分析结论校验 | 分析工程实践2的平均分、及格率和成绩分布 | ready | get_score_summary | verified | PASS |

**汇总：20 条 / 19 PASS / 1 FAIL；其中 5 条完全不调用模型（E07–E09、E16、E17）。**

分类汇总：预设统计 4/4；中文变体 2/3；模糊课程 2/2；明确分布 2/2；班级比较 2/2；
综合分析 2/2；超范围 2/2；协议违规 2/2；数值校验 1/1。

## 3. 唯一失败：E07（真实缺陷，保留为基线失败项）

问题「**工程实践2有没有人不及格**」中，课程 `工程实践2` 确实存在于当前目录，
但语义层返回 `needs_clarification`，消息为「当前课程目录找不到问题中指定的课程」——
**错误信息与实际不符**。

根因（本轮实际复现）：

```
_unresolved_course_reference("工程实践2有没有人不及格", {"工程实践2"})  ->  "有没有人"
_unresolved_course_reference("工程实践2谁不及格",      {"工程实践2"})  ->  ""
```

`src/copilot/agent/semantics.py:70-95` 在移除已知课程名后，把「不及格」之前的疑问短语
「有没有人」当成残留的课程主题；因为目录里没有叫「有没有人」的课程，
`resolve_question` 在 `:250-262` 直接返回澄清，不会执行任何统计。
而「谁不及格」「的成绩」「的平均分和及格率」等表达都能正确 `ready`。

**影响**：一类完全合理的中文统计问法被误拒，且错误提示指向错误的实体。
**建议（下一轮修，本轮不越界改业务语义层）**：在 `_unresolved_course_reference()`
的停用词表中加入疑问短语（有没有 / 有没有人 / 是否 / 谁 / 多少 / 几个 …），
或对提取出的 subject 做「是否包含目录课程名片段」的二次确认；补 3–5 条回归用例。
本轮按用户要求「暂停新增大型功能」，把它如实记录为 **P1 发布前阻断项**。

## 4. 协议违规拦截证据

| ID | 注入内容 | 结果 |
|---|---|---|
| E18 | observations section 夹带自由文本「教学质量好导致均分提升到99.99分」 | `answer_mode=deterministic_fallback`、`analysis_fallback_reason=invalid_analysis`；`99.99` 与「教学质量」均未出现在回答中 |
| E19 | 引用不存在的 `r1.mean_comparison`（本问题没有比较 Evidence） | 同上；`unknown_or_duplicate_claim` 被拦截 |

这说明「模型错误地声称比较或因果关系」在当前协议下无法进入用户回答，
归因于 `analysis.py:285-312` 的严格校验 + `service.py:312-327` 的显式降级。
但请注意：**这里被拒绝的是人工构造的非法输出**，不是真实模型产生的错误；
真实模型的违规率仍未知。

## 5. 为什么不能把 19/20 说成「模型准确率 95%」

- 夹具的「模型」永远按脚本返回，工具调用与 claim 选择都是程序写死的，
  它不会犹豫、不会打错字、不会误解中文。
- 本基线中「综合分析质量 = verified」只证明**校验器与渲染器工作正常**，
  不能证明真实 DeepSeek 会产出合法且有用的选择。
- 真实模型评测必须记录：`answer_mode` 是否为 `evidence_analysis`、
  `analysis_fallback_reason` 分布、claim 选择是否覆盖 mandatory/required tags、
  token 与延迟。这些字段在 `PROTOCOL_ACCEPTANCE.zh-CN.md` 第 4/7 节已定义。

授权后的真实验收可直接复用这 20 条用例，把 `ProtocolFixture` 换成真实 provider，
逐条对比「夹具基线」与「真实模型」的差异，差异本身就是评测结论。

## 6. 复现

```powershell
.\.venv\Scripts\python.exe scripts/phase3d_agent_eval.py `
    docs/development/phase3d-review/results/agent-eval-baseline.json
```

零网络、零付费、无凭证、仅合成数据。结果 JSON 内 `meta.warning` 字段
明确写有「Do NOT report these pass rates as a real-model accuracy figure」。
