# Phase 2B 第一增量：学习与独立审查材料

这份材料补记已经完成的开发，不声称当时已经逐模块提交。起始 Git 提交为 `01dbba2`。本次仅整理文档、导出 Diff 和提交既有成果，没有修改业务逻辑。

## 先阅读哪些文件

1. `docs/development/02b-workbench.md`：按 Step 1–4 记录实现、测试、问题与剩余差距。
2. 本文：三个关键代码片段及调用链。
3. 同目录 `FILES.md`：逐文件新增/修改/删除清单及职责。
4. 同目录 `untracked-source-files.txt`：提交前未跟踪源码原始清单，不会因提交后列表为空而丢失。
5. 导出的审查 ZIP：包含完整当前源码、测试、配置、全部相关提交的累计 Diff 与逐提交补丁。

## 功能与验证边界

| 状态 | 内容 |
|---|---|
| 已实现且已验证 | 六维筛选、课程分段表、及格/不及格图、精确均分分层、CSV、预览、HTTP 页面与 Mock Agent/Evidence 展示、旧库/旧导入兼容。 |
| 已实现，真实服务未在本页重验 | 复用 Phase 2A 的 DeepSeek Provider，页面授权提交入口。此前后端实测成功，不等同本轮网页真实模型实测。 |
| 未验证 | 浏览器像素布局、跨浏览器、移动端、无障碍，以及新页面真实付费模型提交。 |
| 待实现 | Agent 继承专业/类型筛选、任意宽格式 WPS 表适配、缺考原因细分、Word/PDF、完整 36 问评测、Docker。 |

截图只用于理解业务视图，所有运行数据为合成数据，未在学院正式部署。

## 代码一：一次快照生成多种视图

文件 `src/copilot/analytics.py`，方法 `AnalyticsEngine.course_report`。

```python
    def course_report(self, scope: ReportScope, *, expected_version=None) -> CourseReport:
        # All views derive from the same transaction/frame and summarize().
        meta, frame = self.database.snapshot(scope, expected_version=expected_version, reporting=True)
        version = meta["dataset_version"]
        base = scope.base_scope()
        rows = []
        for course_id, group in frame.groupby("course_id", sort=True):
            statistics = summarize(group, version, base.model_copy(update={"course_id": course_id}))
            excellent = int(((group["status"] == "graded") & (group["score_cents"] >= 8000)).sum())
            band = course_mean_band(statistics.sum_score_cents, statistics.graded_count)
            rows.append(CourseReportRow(course_id=course_id, course_name=group.iloc[0]["course_name"],
                        course_type=group.iloc[0]["course_type"], statistics=statistics,
                        fail_rate_percent=ratio(statistics.failed_count * 100, statistics.graded_count),
                        excellent_count=excellent, excellent_rate_percent=ratio(excellent * 100, statistics.graded_count),
                        average_band=band))
        return CourseReport(dataset_version=version, generated_at=datetime.now(timezone.utc).isoformat(), scope=scope,
                            summary=summarize(frame, version, base), rows=rows,
                            mean_bands=[CourseMeanBand(label=label, course_ids=[r.course_id for r in rows if r.average_band == label])
                                        for label, *_ in reversed(BINS)],
                            policy=ReportPolicy(score_bins=[b[0] for b in BINS]))
```

调用链：Streamlit 提交筛选 → DashboardClient.report → GET /api/reports/courses → Pydantic 参数校验 → course_report → Database.snapshot → 现有 summarize。

`reporting=True` 只扩展所取字段和固定筛选，不执行模型生成的 SQL。全部课程行和总计来自同一帧、同一数据版本。均分、分段、及格率继续复用现有 summarize，新增不及格率和优良率仍在后端计算。前端只读这个报告，避免图表与表格各写一份公式。

学习重点：事务快照保障一致性；分组聚合复用现有逻辑；有效成绩是分母，missing/exempt 不补零。总体均分按选课成绩记录计算，并非学生均分的平均。

## 代码二：先精确分类，后舍入展示

文件 `src/copilot/analytics.py`，函数 `course_mean_band`。

```python
def course_mean_band(sum_score_cents: int, graded_count: int) -> str | None:
    """Classify exact rational means before display rounding, using integer comparisons."""
    if not graded_count:
        return None
    for label, lower, upper, inclusive in BINS:
        if sum_score_cents >= lower * graded_count and (
            sum_score_cents <= upper * graded_count if inclusive else sum_score_cents < upper * graded_count
        ):
            return label
    raise ValueError("Mean is outside the valid score range")
```

调用链：course_report → 精确整数总分与有效数量 → course_mean_band → 返回区间标签 → Streamlit mean_bands_table 展示。

分数以百分之一分为整数单位。比较 `sum_score_cents < upper * graded_count` 等价于精确均值小于边界，不需要先除法再舍入。79.995 显示为 80.00，但仍属于 [70,80)。0 条有效成绩返回 None，不能当成低于 60 分。

测试见 test_exact_mean_band_boundaries 和 test_display_rounded_mean_does_not_determine_band。对比截图中的学生成绩分段，这里每个被归类对象是一门课程的平均分。

## 代码三：前端只访问受校验的 HTTP 边界

文件 `src/copilot/dashboard/client.py`，方法 `DashboardClient._request`。

```python
    def _request(self, method, path, model, *, params=None, payload=None):
        try:
            # The backend permits an overall Agent deadline up to 120 seconds.
            with httpx.Client(base_url=self.base_url, timeout=130 if method == "POST" else 10,
                              trust_env=False, follow_redirects=False, transport=self.transport) as client:
                response = client.request(method, path, params=params, json=payload)
        except httpx.TimeoutException:
            raise DashboardError("timeout", "The API request timed out. Check the backend terminal and try again.") from None
        except httpx.TransportError:
            raise DashboardError("connection", "Cannot reach FastAPI. Start the backend on port 8000 and keep its terminal open.") from None
        if not response.is_success:
            code, request_id = "api_error", None
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = body.get("error", {})
                    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
                        code = detail["code"]
                    request_id = body.get("request_id")
            except ValueError:
                pass
            message = ERROR_MESSAGES.get(code, f"The API could not complete this request (HTTP {response.status_code}). Check the backend terminal.")
            raise DashboardError(code, message, request_id=request_id if isinstance(request_id, str) else None)
        try:
            return model.model_validate_json(response.content)
        except (ValidationError, ValueError):
            raise DashboardError("invalid_response", "The API response does not match the expected contract. Restart matching frontend and backend versions.") from None
```

调用链：页面表单 → client.report/query → HTTPX → FastAPI → 返回 JSON → Pydantic model_validate_json → 页面展示或 DashboardError 提示。

前端不打开数据库，也不直接调用 DeepSeek。GET 超时 10 秒；Agent 请求允许 130 秒，覆盖后端最大 120 秒总期限。禁止继承代理和跟随重定向，配置地址不携带 Key。服务端错误正文不直接显示，使用受控提示并保留 request_id。模型的数字与证据验证仍由原 Agent 后端负责，前端类型校验不能代替数值事实校验。

## 实际测试与问题记录

本次重新执行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q --basetemp=.test-tmp-review-packet --junitxml=docs/development/results/phase2b-review-pytest.xml
```

结果：230 passed，0 failed，0 skipped，1 条既有 TestClient 弃用警告，19.79 秒。未调用付费模型。记录文件 phase2b-review-pytest.xml。

上一轮已完成的记录分别为：后端196、页面23、真实HTTP11，全套230；工作台HTTP smoke 15/15；旧HTTP smoke 7/7、旧Agent smoke 6/6。它们是历史已执行结果，本次没有冒称重新执行了这三个独立 smoke 脚本。

已修复的问题包括 Pandas 原生扩展被 Windows 阻止加载、Altair 分层图测试读取位置、隔离 Streamlit 进程缺少 home、前端超时短于后端上限、旧 smoke 隐式依赖 bootstrap 哈希。详细修复过程、失败尝试和舍弃方案见进度记录。没有关闭系统安全策略。

设计取舍：保持两个本地服务和四个受控工具，不引入新框架；专业归属班级、课程类型归属课程是演示模型，真实培养方案还需年级/专业/学期维度；旧数据不猜分类，未知值保持 unspecified；AI 不暗中继承自己尚不支持的筛选。

## 给 ChatGPT 的独立审查任务

可提供导出的 `phase2b-review.zip`，或分别提供 full.diff、commits.patch、FILES.md、本文、测试 XML/HTTP JSON，以及压缩包中的完整 src/tests/config。不要只提供截图或通过数量。

建议提示词：请独立检查该项目的统计分母、精确均分分层、旧库迁移与指纹兼容、受控工具/隐私边界、报表与聊天范围、超时与错误处理、测试是否足以证明结论。按严重程度列出可复现问题，引用文件与行号，区分实际缺陷、设计局限和未验证能力。不要把 Mock 通过视为 DeepSeek 实测。

导出包不包含 .env、数据库、学生记录、API Key 或 .audit 私有日志。完整 Diff 以实际导出时的最终提交为终点，包含所有相关提交；文档和包本身不修改业务逻辑。旧离线结果的未提交差异会单列，避免冒认为本轮功能。
