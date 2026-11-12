# Phase 3D — 真实 DeepSeek 协议验收准备方案（未执行付费调用）

> 状态：**仅准备，未执行**。`live_deepseek_calls = 0`。
> 任何真实请求都需要用户**单独、明确**授权，且必须同时满足脚本内的三重开关。
> 本文件不含 API Key、base URL、账户信息或任何真实学生记录。

## 1. 目的与边界

确认 Phase 3C 新增的 `evidence_analysis_v1` 协议**能否被真实 DeepSeek 正确执行**，并测量它相对确定性统计的真实增益。

本轮完成的是：源码核对、最小可复现方案、离线预演、字段清单、费用与授权设计。
本轮**没有**完成的是：真实模型调用、真实 token/延迟/成本、真实模型准确率。这些必须由用户授权后执行。

**不得把离线夹具的成功率当作真实模型的准确率。** Phase 3C 的
`docs/development/results/phase3c-verification.json` 已明确记录
`live_deepseek_calls: 0`、`unverified: ["live DeepSeek new protocol adherence/quality/latency/cost", ...]`。
用户在更早阶段做过的真实调用属于旧 configured 英文路径，不能证明当前中文证据协议已验证。

## 2. 源码核对结论（本轮实际读到的）

| 结论 | 依据 |
|---|---|
| `provider.py` 在 Phase 3C 的 3 个提交里**未被改动** | `git diff --stat f8526e5..e4674c2` |
| 真实 provider 是 `OpenAICompatibleProvider`，默认 `provider="deepseek"`、`DEEPSEEK_MODEL="deepseek-flash"`、`DEEPSEEK_BASE_URL="https://api.deepseek.com"` | `src/copilot/agent/provider.py:15-16, 43-60, 104-166` |
| 请求体固定 `tool_choice:"auto"`、`stream:false`、`thinking:{"type":"disabled"}`、`max_tokens:2048`、`response_format:{"type":"json_object"}` | `provider.py:117-121` |
| 中文问题自动走 `grounded`；澄清/不支持时 `needs_model=False`，**根本不构造 provider** | `src/copilot/api.py:190-201` |
| 工具与全部参数由 `resolve_question()` 在**模型之前**决定；`validate_intent_call()` 在执行前逐字段比对 | `semantics.py:152, 341-362`、`service.py:277` |
| 模型看到的 user 消息是 ASCII `ACADEMIC_INTENT {...}`；`analysis_context` 随 tool 结果返回 | `semantics.py:52-58`、`service.py:240-244, 294-295` |
| 模型只能返回 `status` / `evidence_ids` / `analysis.sections[].claim_ids` / `style`；中文与数字由 `render_analysis()` 从 `claim.text` 拼接 | `analysis.py:21-46, 285-328`、`models.py:89-111` |
| 校验失败一律 `deterministic_fallback` 并显式标记，不伪装成功 | `service.py:312-327` |
| 后端已有真实状态接口 `/api/agent/status`，返回 `provider/model/simulated` | `api.py:150-156`、`client.py:140-141` |

## 3. 最小可复现方案

脚本：`scripts/phase3d_live_protocol_check.py`（三种模式，默认 `plan`）。

| 模式 | 网络 | 付费 | 做了什么 |
|---|---|---|---|
| `plan`（默认） | 0 | 0 | 构建合成数据、本地解析三条问题、测量请求规模、输出预算；**不构造 provider** |
| `fixture` | 0（`httpx.MockTransport` 进程内回放） | 0 | 走完整 `OpenAICompatibleProvider` → `AgentService` → `render_analysis`，验证全部采集字段 |
| `live` | 真实 HTTPS | **有** | 需 `--i-authorize-paid-calls` + `COPILOT_LLM_PROVIDER=deepseek` + 非空 key；本轮禁用 |

已实测：`--mode live` 无授权旗标时**退出码 1**并拒绝执行；`fixture` 输出
`live_model_calls=0`、`this_run_is_a_real_model_call=false`。

### 三条问题与实测解析（`plan` 输出，见 `results/protocol-plan.json`）

| ID | 问题 | `intent.status` | 工具 | `analysis_goal` | 是否会调用模型 |
|---|---|---|---|---|---|
| C1 | 分析体育 III 的成绩分布。 | `ready` | `get_score_summary` | `distribution` | 是 |
| C2 | 分析体育 III 的成绩表现，有哪些值得关注的地方？ | `ready` | `get_score_summary` | `summary` | 是 |
| C3 | 分析体育课的成绩。 | `needs_clarification` | — | — | **否**（候选：体育 III · 2024级 · 2025-T1） |

C1/C2 的工具参数完全一致：`term="2025-T1"`、`course_id` 为目录中体育 III 的完整 ID、
`class_id/grade_level=null`。C3 由本地语义层直接澄清，`model_turns=0`，因此**最多只有 2 次真实请求**。

合成数据固定为 `SyntheticOptions(classes_per_major=2, students_per_class=6, course_count=6, seed=20261111)`：
12 名学生、2 个班级、6 门课程、72 条成绩记录；体育 III 有效成绩 12 条、均分 85.05、及格率 100.00%、分布 0/0/5/3/4。
`dataset_version` 与 Phase 3C 回执一致，便于逐项对照。

### 请求结构（`plan` 实测字符数）

| 部分 | 规模 | 来源 |
|---|---|---|
| system（GROUNDED_SYSTEM + goal） | 2,756 字符 | `service.py:37-46` |
| 分析协议 PROMPT | 1,889 字符 | `analysis.py:21-46` |
| user（ACADEMIC_INTENT） | 150 字符 | `semantics.py:54-55` |
| tool 结果（含 `analysis_context`） | 4,138 字符 | `service.py:292-296` |
| `analysis_context` claims | 12 条，其中 mandatory 3 条 | `analysis.py:229-275` |

第二turn 会重发前两轮 messages 加 tool 结果，因此单次请求 prompt 规模约 9k 字符
（≈ 2.5k–3.5k tokens，取决于分词），completion 为两个短 JSON（工具参数 + claim 选择）。

## 4. 授权后要逐条记录的字段

| 字段 | 来源 | 说明 |
|---|---|---|
| `provider` / `model` / `simulated` | `AgentResponse` / `/api/agent/status` | 必须为 `deepseek` / 实际 model ID / `false` |
| 请求工具及参数 | `result.tool_calls[].tool/arguments`、`result.evidence[].arguments` | 应与 C1/C2 表中一致 |
| `model_turns` | `AgentResponse.model_turns` | 正常为 2；上限 `MAX_MODEL_TURNS=4` |
| `answer_mode` | `AgentResponse.answer_mode` | 期望 `evidence_analysis` |
| `fallback_reason` / `analysis_fallback_reason` | 同上 | 成功时应为 `null` |
| `analysis_status` | 同上 | 期望 `verified` |
| AI 选择的 claim IDs | `result.analysis.sections[].claim_ids` | 必须全部出现在 `analysis_context.claims` 中 |
| 最终中文分析 | `result.answer` | 服务端渲染的四段中文 |
| Evidence 一致性 | 脚本 `evidence_consistency()` | 版本一致、引用的 evidence_id 有效、数字可追溯到 Evidence/claim facts |
| Token 使用量 | `result.usage`、`result.model_trace[].usage` | 真实模型才非空；Mock 恒为 `null` |
| 耗时 | 脚本 wall clock + `tool_calls[].elapsed_ms` | 真实延迟 |
| 成本 | 官方账单/价目表 | 脚本**恒置 null**，不猜价格 |

`fixture` 模式已把这条采集链完整跑通（见 `results/protocol-fixture-dryrun.json`）：
C1 选 6 个 claim、C2 因 `goal=summary` 多选 `r1.mean`/`r1.pass_rate` 共 7 个，
C3 为 `local_notice`；三条的 `version_aligned / evidence_refs_valid / selected_claims_exist /
answer_numbers_traceable_to_evidence` 均为 `true`。
**该文件中的 `usage` 与 `elapsed_ms` 是脚本注入的回放值，不是 DeepSeek 遥测**（文件内有
`measurement_note` 明确标注）。

## 5. 费用控制与预期量级

硬上限（源码/脚本双重约束）：

- 最多 **2 次**真实请求（C3 本地澄清，不构造 provider）。
- 单次 `MAX_MODEL_TURNS=4`（`service.py:22`）、`max_tokens=2048`（`provider.py:119`）、超时 45s。
- **自动重试 0 次**；任何 401/402/429/超时都直接记录为失败，不重跑。
- 只用合成数据，不上传任何成绩单，不外发中文原句、姓名或学号。

量级估算：约 8k–12k prompt tokens + 约 0.4k–0.8k completion tokens（两次请求合计）。
第三方资料（2026-08-17 调价报道，非官方账单）给出的 V4-Flash 空闲时段参考价约为
输入 ¥1.5/百万、输出 ¥4.5/百万，高峰时段翻倍；按此量级单次总额在**分币级**。
**这些数字只用于预算上限判断，不作为报价**：实际费用以授权后账户账单为准，脚本不计算成本。

零付费预检（授权前即可做，不消耗 token）：

```powershell
.\scripts\check_deepseek_key.ps1 -Prompt   # GET /user/balance；不打印 key、不打印余额、不消耗 token
```

### 授权前必须确认的一个风险

源码默认 `DEEPSEEK_MODEL = "deepseek-flash"`。项目 11-12 早些时候的操作记录曾确认该 ID 有效、
并做过用户授权的真实调用；但本轮检索到的第三方资料把当前模型写作 `deepseek-v4-flash`
（旧 `deepseek-chat` / `deepseek-reasoner` 别名据报道已退役）。两者可能只是别名/版本命名差异，
**不能据此断定默认 ID 已失效**，但也不该在付费验收里赌它有效。执行前用官方 `/models`
或账户后台复核一次；如不同，通过 `COPILOT_LLM_MODEL` 覆盖，不改源码。
`.\scripts\check_deepseek_key.ps1 -Prompt` 的余额接口同时能确认凭证是否可用（零 token）。

## 6. 需要用户明确授权的具体操作

请逐项确认（回复中不需要提供任何密钥）：

1. 授权执行**一次**真实 DeepSeek 协议验收，范围严格限定为 C1/C2/C3 三条问题、最多 2 次付费请求。
2. 确认可用的 model ID（默认 `deepseek-flash` 或指定 `COPILOT_LLM_MODEL`）。
3. 确认已在后端服务窗口配置有效 key，并已用零付费脚本确认账户可用。
4. 确认允许把**脱敏后的聚合结果**（provider、model、工具参数、claim IDs、中文分析、token、
   耗时）写入 `docs/development/phase3d-review/results/protocol-live.json`。

授权后我将执行（key 不由我读取、不打印、不落盘）：

```powershell
.\.venv\Scripts\python.exe scripts/phase3d_live_protocol_check.py --mode live `
    --i-authorize-paid-calls --output docs/development/phase3d-review/results/protocol-live.json
```

## 7. 验收判定标准

**协议被正确执行**（全部满足才算通过）：

- C1/C2：`provider="deepseek"`、`simulated=false`、`model_turns<=4`、
  `answer_mode="evidence_analysis"`、`analysis_status="verified"`、
  `analysis_fallback_reason=null`、`usage` 非空。
- `claim_ids` 全部存在于 `analysis_context.claims`，覆盖全部 `mandatory_claim_ids`、
  `required_goal_tags`，且 `distribution`/`summary` 目标对应的观察标签存在。
- `evidence_consistency` 四项全为 `true`；中文分析中每个统计数值都能追溯到 Evidence 或派生 claim facts。
- C3：`status="needs_clarification"`、`model_turns=0`、无 provider 构造、无 Evidence。

**协议失败**（必须如实记录，不得算通过）：任一 C1/C2 出现
`deterministic_fallback`；此时记录 `analysis_fallback_reason`
（`missing_analysis` / `invalid_analysis`）与拒绝原因（`missing_section`、
`unknown_or_duplicate_claim`、`missing_requested_metric` 等），作为真实模型协议遵循度的证据。

## 8. 安全边界

- 不打印、不记录 API Key、base URL、账户余额或任何敏感配置；`LLMSettings.api_key` 用 `SecretStr`。
- 只使用服务端生成的合成数据；不读取真实成绩单、姓名、学号或个人成绩。
- 输出文件只含聚合统计、内部目录 ID、版本哈希、claim IDs 与服务端渲染的中文结论。
- `tests/conftest.py` 强制 `COPILOT_LLM_PROVIDER=mock` 且删除 key 环境变量，
  因此 pytest 永远不会发起付费调用；验收脚本运行在 pytest 之外，靠显式开关控制。
