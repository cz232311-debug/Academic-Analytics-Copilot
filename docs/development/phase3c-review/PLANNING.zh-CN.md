# 受控自由问题规划：设计与离线验证

本轮只交付设计和一个不执行工具的验证原型。`agent/planning.py` 没有被 `AgentService`、FastAPI 或页面引用；没有上线“复杂问题自动拆解”能力，也没有对外新增规划接口。没有真实 DeepSeek 调用。

## 当前生产链路与未来规划的区别

生产链路仍是：用户问题 → 本地目录语义解析 → 一个明确的 `ResolvedIntent` → 模型提出工具调用 → Pydantic 参数验证 → `validate_intent_call()` 精确核对 → 原有统计工具 → Evidence。歧义需要澄清，不能由模型自行扩大范围。

未来可以考虑：先由本地语义层及用户确认形成少量已授权步骤，再让模型选择这些步骤的顺序。一个模型计划最多包含三个节点，每个节点只有 `node_id`、已有四工具之一和结构化参数。它不包含 SQL、Python、自由指令或可执行代码。

```json
{
  "version": "academic_plan_v1",
  "nodes": [
    {
      "node_id": "summary",
      "tool": "get_score_summary",
      "arguments": {
        "term": "2026-T1",
        "course_id": "G1-C01",
        "class_id": null,
        "grade_level": null
      }
    }
  ]
}
```

`node_id` 必须存在于服务器提供的 `approved_nodes`。模型不能通过换一个节点名获得新权限。每个已确认节点的课程、班级、专业、年级、学期和数据版本，均来自现有本地解析结果。

## 原型实际验证了什么

`validate_plan()` 先验证严格的 Pydantic 结构、节点数量、唯一性、授权节点和数据版本，再逐个调用原有的 `ToolRegistry.validate()` 及原封不动的 `validate_intent_call()`。它返回验证后的计划，不执行统计，不访问外部模型，不产生新的 Evidence。

`tests/test_phase3c_planning.py` 用脚本化 Mock JSON 作为模型输出，包含 **16 个离线案例**：

| 案例 | 数量 | 实际结果 |
| --- | ---: | --- |
| 一个已确认节点、两个节点重排、三个白名单节点 | 3 | 接受并保留顺序 |
| 未确认节点、重复节点、超出三节点上限 | 3 | 拒绝 |
| 任意 SQL 工具、额外 Python 参数、自由解释字段 | 3 | 拒绝 |
| 替换课程、把课程设为 null 扩大范围、替换学期或班级 | 4 | 拒绝 |
| 过期数据版本、尚待澄清的意图、重复 JSON 键 | 3 | 拒绝 |

实际执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase3c_analysis.py tests/test_phase3c_planning.py -q -p no:cacheprovider
```

执行结果：**59 passed in 2.87s**，其中分析协议 43 个案例、规划原型 16 个案例。后续合并后的完整回归结果由本轮评审记录另外列出。

## 尚未实现与上线前条件

该测试没有证明模型能够把任意中文组合问题正确拆解成计划。原型的 `approved_nodes` 是测试在服务器本地预先明确生成的，不是让模型自行声明授权。

真正接入生产前，仍需独立设计：从复杂中文问题提取所有明确实体、展示多步骤范围供用户确认、限定每个节点权限、导入来源与外部模型守卫、统一数据版本快照、执行预算、失败时的整体状态及每个结论的 Evidence 绑定。用户未明确的课程或班级仍须澄清。需要新增独立评测和明确授权后，才能开展少量真实 DeepSeek 验证。

本轮没有放松现有工具白名单、意图验证、数据来源隐私限制或版本校验，也没有引入 LangChain、多 Agent、RAG 或新的统计逻辑。
