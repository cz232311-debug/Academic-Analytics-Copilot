# Phase 3D — 真实 AI 分析协议验收准备与发布前质量检查

> 日期：2026-11-12 · 分支 `feature/chat-first-agent` · HEAD `e4674c2`
> 性质：**只读评估 + UI 真实性修复 + 验收准备**，暂停新增大型功能。
> 本轮 **真实 DeepSeek 付费调用 = 0**；未读取或打印任何 API Key；未改动业务统计/语义逻辑；**未提交 Git**。

---

## 1. 当前 Git 分支与 commit

| 项 | 值 |
|---|---|
| 分支 | `feature/chat-first-agent`（`main` 未动） |
| HEAD | `e4674c2626576d8bc781733623ad90ba31a89b92` |
| 分支提交总数 | 37 |
| 最近 5 次提交 | `e4674c2` Phase 3C 诊断与学习包 · `e610a0e` 经验证的中文证据分析与显式回退 · `6b68660` 课程意图守卫与当前合成快照导出 · `f8526e5` 原表坐标预览与上传数据集沿用 · `b52bac1` key trim 与零付费自检 |
| 本轮基线复验 | 独占全量 **584 passed / 160.99s** |
| 本轮最终回归 | 独占全量 **596 passed / 139.10s**（+12 个新增 UI 测试），0 失败 0 跳过，1 个既存 `StarletteDeprecationWarning` |

工作区状态（`git status --short`）：

- 已修改：`README.md`、`START_HERE.zh-CN.md`、`src/copilot/dashboard/chat.py`、
  `tests/test_chat_app.py`、`tests/test_phase3c_chat.py`。
- 新增：`scripts/phase3d_live_protocol_check.py`、`scripts/phase3d_ab_compare.py`、
  `scripts/phase3d_agent_eval.py`、`tests/test_phase3d_ui_status.py`、
  `docs/development/phase3d-review/`（4 份文档 + 4 个结果 JSON）。
- 原有、非本轮产生且**故意不提交**：owner-owned
  `docs/development/results/14-deepseek-offline-demo.json`、`.workbuddy/`、
  `docs/development/phase3b-review.zip`、`docs/development/phase3c-review.zip`、
  上一轮生成的 `docs/development/CURRENT_SYSTEM_HANDOFF.md`。

**本轮未提交 Git**：任务书未要求提交，`main` 未动，等待用户确认后再决定是否提交。

---

## 2. 实际已完成的验收与未验证事项

| 类别 | 内容 |
|---|---|
| **已完成并验证（离线）** | Phase 3C 基线 584 项复验；最终 596 项全量回归；UI 六种状态 33 项定向回归；三条真实验收问题的本地意图解析；`plan`/`fixture` 两模式零网络预演；20 条 Agent Evaluation 基线；同一快照 A/B 结构化对照；Evidence 数字一致性、协议违规拦截、版本对齐 |
| **已实现但只经夹具验证** | `evidence_analysis_v1` 的校验与中文渲染 — 所有 `verified` 结果都来自 scripted provider，**不是真实模型输出**。Phase 3C 回执自己写明 `live_deepseek_calls: 0`、局限为「not DeepSeek output quality」 |
| **已设计并写好脚本、尚未执行** | 真实 DeepSeek 协议验收（`--mode live`），需用户单独授权 |
| **明确未验证** | 真实模型协议遵循度、claim 选择质量、fallback 率、真实 token/延迟/成本；Agent Evaluation 在真实模型上的表现；连续多轮对话；移动端/暗色主题/跨浏览器/无障碍；`planning.py` 受控规划的生产链路（无生产调用方） |
| **本轮明确未做** | 未发起付费调用；未修改统计引擎、语义层、provider、工具白名单、隐私守卫；未新增多模型/多 Agent/RAG/Word 报告/统计工具 |

一个必须写清的事实：`provider.py` 在 Phase 3C 的 3 个提交里**未被改动**。
默认离线 `MockDemoProvider` 拿到工具结果后只返回 `evidence_ids`，**不返回 `analysis`**，
所以默认 Mock 下新协议必然 `missing_analysis` → `deterministic_fallback`。
这是设计使然，不是故障，但也不能算「AI 分析已跑通」。

---

## 3. 真实模型测试方案及预期费用控制

完整方案见 [`PROTOCOL_ACCEPTANCE.zh-CN.md`](PROTOCOL_ACCEPTANCE.zh-CN.md)，
脚本 `scripts/phase3d_live_protocol_check.py`，预演结果在 `results/protocol-plan.json` 与
`results/protocol-fixture-dryrun.json`。

**三条问题与实测解析**（同一合成快照：12 人 / 6 课 / 72 条，体育 III 均分 85.05、及格率 100.00%）：

| ID | 问题 | 解析结果 | 工具 / goal | 调用模型 |
|---|---|---|---|---|
| C1 | 分析体育 III 的成绩分布。 | `ready` | `get_score_summary` / `distribution` | 是 |
| C2 | 分析体育 III 的成绩表现，有哪些值得关注的地方？ | `ready` | `get_score_summary` / `summary` | 是 |
| C3 | 分析体育课的成绩。 | `needs_clarification`（候选「体育 III」） | — | **否** |

因此**最多 2 次真实请求**。硬预算：单次 `MAX_MODEL_TURNS=4`、`max_tokens=2048`、
超时 45s、**自动重试 0 次**；请求规模实测 system 2,756 + protocol 1,889 + user 150 +
tool 结果 4,138 字符，两轮合计约 8k–12k prompt tokens、0.4k–0.8k completion tokens。
第三方资料（2026-08 调价报道，非官方账单）给出的参考价对应**分币级**总额；
脚本 `cost` 恒为 `null`，实际费用以授权后官方账单为准。

**授权闸已实测**：`--mode live` 缺 `--i-authorize-paid-calls` 时退出码 1 并拒绝；
`fixture` 模式输出 `live_model_calls=0`、`this_run_is_a_real_model_call=false`，且
`measurement_note` 明确声明其中的 `usage`/`elapsed_ms` 是注入的回放值、不是 DeepSeek 遥测。

**执行前需确认 model ID**：源码默认 `deepseek-flash`，项目早先记录确认其有效并做过真实调用；
本轮第三方资料把当前模型写作 `deepseek-v4-flash`。两者可能是别名差异，但付费验收前应
用官方 `/models` 或零付费 `.\scripts\check_deepseek_key.ps1 -Prompt` 复核一次，不改源码。

---

## 4. 需要用户授权的具体操作

请逐项明确确认（**回复中不需要、也不应提供任何密钥**）：

1. 授权执行**一次**真实 DeepSeek 协议验收，范围严格限定 C1/C2/C3，最多 2 次付费请求、无重试。
2. 确认可用 model ID（默认 `deepseek-flash`，或指定 `COPILOT_LLM_MODEL`）。
3. 确认后端服务窗口已配置有效 key，账户余额可用（建议先跑零付费自检脚本）。
4. 确认允许把**脱敏聚合结果**（provider、model、工具参数、claim IDs、中文分析、token、耗时）
   写入 `docs/development/phase3d-review/results/protocol-live.json`。

授权后执行的命令（key 由用户自行配置，我不读取、不打印、不落盘）：

```powershell
.\.venv\Scripts\python.exe scripts/phase3d_live_protocol_check.py --mode live `
    --i-authorize-paid-calls --output docs/development/phase3d-review/results/protocol-live.json
```

判定标准：C1/C2 需 `simulated=false`、`answer_mode=evidence_analysis`、
`analysis_status=verified`、`analysis_fallback_reason=null`、`usage` 非空，且 claim IDs
全部来自 `analysis_context.claims` 并覆盖 mandatory/required tags；C3 需 `model_turns=0`、
无 provider；任一 fallback 都记录 `analysis_fallback_reason` 并计入**协议失败**，不算通过。

---

## 5. 自动化评测结果

| 验证 | 结果 | 证据 | 是否涉及真实模型 |
|---|---|---|---|
| 基线全量回归 | **584 passed / 160.99s**，0 失败 | 独占 pytest，`.test-tmp-phase3d-baseline` | 否 |
| 最终全量回归 | **596 passed / 139.10s**，0 失败 0 跳过，1 既存警告 | 独占 pytest，`.test-tmp-phase3d-final` | 否 |
| UI 定向回归 | **33 passed / 38.14s** | `test_phase3d_ui_status.py` + 两个既有 UI 模块 | 否（`LiveLikeProvider` 纯进程内回放） |
| 真实模型方案预演 `plan` | 3 条意图全部符合预期，2 条会调用模型 | `results/protocol-plan.json` | 否，未构造 provider |
| 采集链路预演 `fixture` | C1 选 6 claim、C2 选 7 claim（含 mean/pass）、C3 `local_notice`；Evidence 一致性 4 项全 `true` | `results/protocol-fixture-dryrun.json` | 否，`httpx.MockTransport` |
| Agent Evaluation 基线 | **20 条 / 19 PASS / 1 FAIL**，5 条完全不调用模型 | `results/agent-eval-baseline.json` | **否**，scripted fixture |
| A/B 对照 | A 180 字符/2 个统计值；B 416 字符/4 个；B 有局限说明、A 没有；两者 `model_turns=2` | `results/ab-comparison.json` | 否 |
| 协议违规拦截 | E18 自由文本、E19 不存在的比较 claim 均 `deterministic_fallback`；`99.99`/「教学质量」未进入回答 | 同上基线 JSON | 否 |
| 真实 DeepSeek API 测试 | **未执行** | `live_deepseek_calls: 0` | — |
| Agent Evaluation（真实模型） | **未执行** | — | — |

⚠️ 评测基线的 `meta.warning` 明确写有「不得把该通过率当作真实模型准确率」。
夹具永远按脚本返回，不会误解中文、不会选错 claim；19/20 只说明本地语义层与协议校验器工作正常。

**唯一失败 E07**：「工程实践2有没有人不及格」被误判「找不到课程」。根因已实测：
`_unresolved_course_reference()`（`semantics.py:70-95`）把疑问短语「有没有人」当成残留课程主题，
而「谁不及格」「的成绩」正常。本轮按「暂停新增大型功能」要求**未越界修改语义层**，
保留为基线失败项并列入下方阻断清单。

---

## 6. UI 修复与测试结果

问题：`_mode_label()` 只要是合成数据就固定写「DeepSeek AI 分析（合成数据）」，
完全不感知后端是 Mock 还是真实 DeepSeek；同时 Mock 夹具选出的 claim 会被标成
「AI 综合分析（证据校验通过）」，默认 Mock 的 `missing_analysis` 又被描述成「模型失败」。
确认按钮/单选项也承诺「可体验 DeepSeek」。

改动（`src/copilot/dashboard/chat.py`，+136 −40 含测试）：

- 新增 `_agent_runtime()`：**仅在合成数据已确认时**读取 `/api/agent/status`，按
  `dataset_version` 缓存；上传数据不请求，因此首屏 `/health` + `/api/dataset`、
  上传沿用的精确路径契约均未被破坏。数据集切换会清空缓存。
- 侧栏新增「**本次分析状态说明**」：显示真实 `provider` / `model` / `simulated` 与是否外发，
  并列出六种状态的对照说明。
- `_mode_label()` / `_answer_mode_label()` 改为按回答自身的
  `provider` / `simulated` / `analysis_status` 判定；保留「离线 Mock 演示」「DeepSeek AI 分析」
  字样以兼容既有契约，并追加精确后缀。
- 六种状态现在可区分：合成+Mock、合成+真实 DeepSeek、上传+本地规则、
  澄清/不支持（未调用模型）、模型分析成功、模型分析失败 fallback。
  离线夹具的 verified 显示「证据综合分析（离线 Mock 夹具 · 非真实模型输出）」；
  Mock 的 `missing_analysis` 显示「预期行为，不是真实模型失败」。
- 文案去承诺化：「合成示例数据（虚构数据）」「确认并启用合成数据集」；
  修正首页「AI 只负责选择工具与组织语言」这句与源码不符的表述。
- **保留**「计算证据与技术详情」中的完整 `course_id`、`dataset_version`、Evidence 与执行记录。

测试：`tests/test_phase3d_ui_status.py` 新增 12 个用例，用 `LiveLikeProvider`
（`simulated=False` 但零网络）覆盖真实模型分支；同步更新 `test_chat_app.py`、
`test_phase3c_chat.py` 中两处固化旧误导文案的断言。定向 33 passed，全量 596 passed。

---

## 7. GitHub 发布前阻断问题清单

| 级别 | 问题 | 证据 | 影响 | 建议 |
|---|---|---|---|---|
| **P0-1** | 真实模型协议验收未完成 | `phase3c-verification.json: live_deepseek_calls=0`；本轮同样 0 | 不能对外宣称「DeepSeek 综合分析已验证」；面试演示若被追问会站不住 | 授权执行一次 2 请求验收；或所有材料统一声明「离线 Mock / scripted 演示」 |
| **P0-2** | 「AI 综合分析」的能力表述与实现不符 | `analysis.py` 模型只选 claim IDs，中文/数字全由服务端渲染 | 简历或 README 若写成「LLM 生成分析报告」属于夸大 | 统一话术为「Evidence-grounded 受控分析：模型选择证据要点，服务端渲染可核验结论」 |
| **P1-1** | 中文疑问短语被误判为课程名 | E07 实测；`semantics.py:70-95` | 一类合理中文问法被拒，且错误提示指向错误实体 | 停用词表补疑问短语 + 3–5 条回归用例 |
| **P1-2** | 中文紧贴 ASCII ID 时 `\b` 边界不匹配 | 上一轮审计实测：带空格 `ready`、无空格 `needs_clarification` | 示例问题需靠空格规避，用户自然书写易失败 | 改 `\b` 为针对 ASCII token 的自定义边界 |
| **P1-3** | 干净克隆可复现包缺失 | 现有 README/START_HERE 分散，无一键 quickstart | 外部导师/面试官无法 3 分钟内跑起来 | 补一键测试命令 + 3 分钟演示脚本 + 明确 Mock 默认 |
| **P1-4** | model ID 未复核 | 源码 `deepseek-flash`，第三方资料 `deepseek-v4-flash` | 付费验收可能直接 4xx | 官方 `/models` 复核；必要时用环境变量覆盖 |
| **P1-5** | 历史文档数字过时 | `CURRENT_SYSTEM_HANDOFF.md` 全部写 461 passed / `f8526e5`；`02b-workbench.md:3` 写 456 tests；`02b-workbench.md:220` 写 584 | 交给外部导师时会读成当前状态 | 在这些文档顶部加「快照日期 + 当时 HEAD」声明，或更新为 596 / `e4674c2` |
| **P2-1** | `chat.py` docstring 指向不存在的 `workbench_app.py` | `chat.py:11`；实际入口是 `streamlit_app.py` | 误导新人 | 改注释 |
| **P2-2** | `state["scope"]` 只写不读 | `chat.py:85, 664, 671`，无读取点 | 会话范围面板删除后的残留状态 | 清理或明确标注为预留 |
| **P2-3** | `AgentResponse.stages` 恒空、`streaming=False` | `models.py:199`、`api.py:156` | 无分阶段进度/流式输出 | 保持现状并在文档中说明，不要伪造阶段日志 |
| **P2-4** | `planning.py` 只有测试、无生产调用方 | `grep` 确认 `src/` 无 import | 受控自由问题规划仍是设计，不是功能 | 文档已标注「未启用」，继续保持 |
| **P2-5** | 移动端/暗色主题/跨浏览器/无障碍未验收 | Phase 3C 回执 `unverified` | 影响展示面 | 发布前做一次基础验收 |
| **P2-6** | 无多用户鉴权，仅本机演示 | 架构现状 | 不能作为在线服务发布 | README 明确「本地演示」边界 |

---

## 8. 对最终目标的直接回答

**问：Phase 3C 的新证据分析协议能否被真实 DeepSeek 正确执行？**
**答：目前不能确认，也不应声称已确认。** 已有的 43+ 项协议测试、Phase 3C 浏览器验收、
本轮 20 条基线与 `fixture` 预演，全部使用 scripted provider。协议**校验器**与**渲染器**已被充分验证；
真实模型能否稳定返回合法 claim 选择、fallback 率是多少、token/延迟/成本如何，只有授权后的
2 次真实请求能回答。方案、脚本、字段、判定标准、授权闸已全部就绪。

**问：新 AI 分析相对确定性统计的真实增益是什么？**
**答：可确认的是「更好的产品回答」，尚不能确认「更强的 AI」。**
B 比 A 多出 2 个派生比例（41.67%、58.33%）、口径说明、样本/范围局限与后续建议，
且 A 完全没有局限说明；但这些内容**全部由 `analysis.py` 从 Evidence 确定性派生**，
两条路径 `model_turns` 都是 2，B 不增加模型轮次。模型的唯一贡献是从 12 条预写 claim 中
选择、排序、选 style。真实增益的验证点因此非常具体：**真实模型能否稳定产出有效且更有重点的
claim 选择，而不是频繁回退**。若 fallback 率高，实际用户价值可能退化为「和 A 一样，但多一次无效协议往返」。

**发布/面试演示前的最小必要事项**：① 授权一次真实验收或明确声明 Mock；
② 统一「AI 能力」话术；③ 修 P1-1/P1-2 两条中文解析；④ 干净克隆一键复现包；
⑤ 给过时文档加快照日期声明。

---

## 附：本轮交付文件

| 文件 | 内容 |
|---|---|
| `docs/development/phase3d-review/PHASE3D_REVIEW.zh-CN.md` | 本报告（7 项交付） |
| `PROTOCOL_ACCEPTANCE.zh-CN.md` | 真实模型测试方案、字段清单、费用控制、授权清单、判定标准 |
| `AB_COMPARISON.zh-CN.md` | 五维 A/B 评估与「确定性工程 vs AI」结论 |
| `AGENT_EVAL_BASELINE.md` | 20 条基线、E07 根因、不得当作模型准确率的声明 |
| `results/protocol-plan.json` | 纯本地意图解析与请求规模预演 |
| `results/protocol-fixture-dryrun.json` | 完整采集链路预演（零网络） |
| `results/ab-comparison.json` | A/B 结构化结果 |
| `results/agent-eval-baseline.json` | 20 条评测明细 |
| `scripts/phase3d_*.py` | 三个可复现脚本 |
| `tests/test_phase3d_ui_status.py` | 12 个新增 UI 状态回归 |
