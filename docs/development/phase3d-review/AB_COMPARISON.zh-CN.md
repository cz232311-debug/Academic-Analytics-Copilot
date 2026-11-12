# Phase 3D — A/B 对照：确定性统计 vs 受控证据分析

> 同一份合成数据快照（`dataset_version = 92ca8e1e…`，12 名学生 / 6 门课 / 72 条成绩）。
> 问题：`分析体育 III 的成绩分布`（有效成绩 12 条、均分 85.05、及格率 100.00%、分布 0/0/5/3/4）。
> **B 的 claim 选择来自离线脚本夹具，不是真实 DeepSeek 输出；真实模型增益尚未测量。**
> 可复现脚本：`scripts/phase3d_ab_compare.py`，结构化结果：`results/ab-comparison.json`。

## 1. 两种回答的来源

| | A：原确定性统计 | B：Phase 3C 受控证据分析 |
|---|---|---|
| 生成函数 | `render_fallback()` + 前端 `_evidence_cn()` | `render_analysis()` |
| 谁写中文 | 服务端模板（前端中文结论 / 后端英文回退） | 服务端模板（`claim.text` 拼接） |
| 谁写数字 | `AnalyticsEngine` 的 Evidence | `AnalyticsEngine` 的 Evidence + `analysis.py` 派生比例 |
| 模型做了什么 | 无（Mock 只回 `evidence_ids`） | 选择 claim IDs、排序、选 style |
| `model_turns` | 2 | 2（**没有增加模型轮次**） |
| 字符数 | 180 | 416 |
| 出现的统计值 | 85.05、100.00 | 85.05、100.00、41.67、58.33 |

## 2. 五个维度的评估

### ① 是否比指标罗列提供更多信息 —— **是，但来自确定性代码，不是模型洞察**

A 只有指标与原始分段计数。B 额外给出：

- 最多分数段 70–80 分共 5 条，占 **41.67%**；
- 80 分及以上 7 条，占 **58.33%**；
- 「均分、及格率和分布均以有效成绩记录为统计对象」的口径说明；
- 小样本与范围局限；
- 后续建议。

这四项**全部**由 `analysis.py` 的 `_stat_claims()` 从 Evidence 计算并写成固定中文句子
（`analysis.py:136-190`）。模型没有"发现"任何新事实，只是决定让哪些预写句子出现、以什么顺序出现。
因此「信息量增加」应归功于确定性派生指标与文本工程，而非 AI 推理能力。

### ② 是否强调真正值得关注的统计特征 —— **部分**

`goal=distribution` 会强制要求 observations 中存在带 `distribution` 标签的 claim，
否则 `render_analysis()` 抛 `missing_distribution_observation` 并回退；本次夹具还用了
`style="focus"`，使观察段落以「值得关注的是…」开头。

也就是说，**重点由协议约束和夹具保证，不是模型自主判断**。真实模型是否会在 12 条 claim 里
挑中 `dominant_band` / `high_band`，而不是只挑均分与及格率，目前没有证据。

### ③ 是否正确说明样本和分析范围局限 —— **是，这是最明确的增量**

B 的 mandatory claim `r1.limits` 明确写出：

> 本次仅有 12 条有效成绩，样本较小；这些结果仅描述本次已确认范围和已导入记录，
> 不能据此推断其他学期、未记录学生或教学原因。

A **完全没有任何局限说明**（脚本检查 `A_has_any_limitation = false`）。
对一份会被人拿去做判断的成绩分析，这个增量比多两个百分比更有价值——但它同样来自
`analysis.py:148-153` 的固定规则，不是模型临场判断。

### ④ 是否包含无根据的比较、归因或结论 —— **未发现**

- 逐句扫描肯定式因果词（因为 / 导致 / 显著提升 / 证明 / 预测 / 必然…）：**0 处**。
  因果相关措辞只以否定形式出现：「并不表示教学效果的因果结论」「不能据此推断…教学原因」。
- 未选中任何带 `comparison` 标签的 claim（本问题没有比较 Evidence）。
- B 中 4 个带小数的统计值全部可在 `evidence.data` 或派生 `claim.facts` 中找到
  （`numbers_all_traceable = true`）。

这来自硬约束：模型不能写自由文本，只能引用已存在的 claim ID；任何编造的 ID、
重复 ID、kind 不匹配、缺 mandatory claim 都会在统计执行后、渲染前被拒绝并回退
（`analysis.py:285-312`）。Phase 3C 已有 43 个针对伪造/越权/缺项的回归测试。

### ⑤ AI 的额外调用是否产生有意义的用户价值 —— **设计上有价值，真实增益未验证**

- 两条路径的 `model_turns` 都是 2：B **没有增加模型轮次**，只是把最后一轮的输出从
  「`evidence_ids`」换成「四段 claim 选择 JSON」，completion tokens 会略增。
- 因此成本增量很小，而输出在结构上更完整（事实 / 观察 / 局限 / 建议）。
- **但**：到目前为止所有 `analysis_status=verified` 的结果都来自 scripted fixture。
  Phase 3C 回执自己写明：「Scripted provider is a deterministic test fixture selecting claims;
  this verifies backend validation and frontend rendering, **not DeepSeek output quality**」。
  默认离线 Mock 甚至不产出新协议，必然 `missing_analysis` 回退。

真实模型可能：选错 claim、漏 mandatory claim、返回自由文本、引用不存在的 ID，
从而触发 fallback。若 fallback 率很高，B 的实际用户价值可能退化为「和 A 一样，但多一次无效的协议往返」。
**这个数字只有授权后的真实请求才能测出来。**

## 3. 结论

| 判断 | 答案 |
|---|---|
| B 比 A 信息更多吗 | 是（多 2 个派生比例 + 口径 + 局限 + 建议） |
| 这些多出来的信息是模型算的吗 | **不是**，全部由确定性 Python 从 Evidence 派生 |
| 模型的真实职责是什么 | 从 12 条预写 claim 中选择、排序、选 style；不写中文、不写数字 |
| 相对 A 的用户价值在哪 | 结构化分段、强制范围/样本局限、按 goal 组织重点 |
| 真实模型能否稳定做到 | **未验证**，这是 Phase 3D 唯一尚未回答的核心问题 |
| 是否可以对外宣称「AI 具备综合分析能力」 | **不可以**。应表述为「Evidence-grounded, 服务端渲染的受控分析；真实模型协议遵循度待验收」 |

一句话：**B 是更好的产品回答，但目前是更好的确定性工程，不是更强的 AI。**
判断新协议是否成功，要看真实 DeepSeek 能否稳定产出有效的 claim 选择，
而不是看夹具渲染出的中文有多通顺。

## 4. 与授权后真实验收的衔接

`scripts/phase3d_live_protocol_check.py` 的 C1 与本对照使用同一份快照和同一问题。
授权后可把 `live` 输出的 `claim_ids` 与本文夹具的 8 个 claim 逐一比对：

- 选中集合不同 → 观察模型是否仍覆盖 mandatory / required tags / distribution 观察；
- `analysis_status=fallback` → 记录 `analysis_fallback_reason`，计入协议失败率；
- `usage` 与耗时 → 与 A 路径比较，判断额外成本是否值得。

对照脚本 `results/ab-comparison.json` 中的
`assessment.5_value_of_the_extra_model_call.verdict =
"design_value_only_real_gain_unverified"` 就是当前状态的机器可读标记。
