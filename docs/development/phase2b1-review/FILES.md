# Phase 2B.1 文件清单

基线：`c58545db2fed83fed3eb28e9d6d5eb451667feac`。最终检查点及逐提交patch由ZIP manifest固定。

本轮没有删除文件。没有修改AnalyticsEngine、schema.sql、Agent核心或依赖锁文件。用户原有离线回执不属于下表。截图、JUnit、文档不等于运行实现；边界见[开发记录](../02b1-workflow.md)。

| 变化 | 文件 | 责任 |
|---|---|---|
| 修改 | `.gitignore` | 排除本地私有.import-history原件与回执。 |
| 修改 | `README.md` | 当前工作流、无默认播种、验证边界及历史Agent显式启动说明。 |
| 修改 | `START_HERE.zh-CN.md` | 工作人员本地启动、上传核对、分析及限制说明。 |
| 修改 | `docs/development/02b-college-import.md` | 指向后续工作流及私有留存更新，保留原轮次证据。 |
| 修改 | `docs/development/02b-workbench.md` | 阶段进展索引；旧行为明确标为历史。 |
| 新增 | `docs/development/02b1-workflow.md` | 实现/实测/待验证边界、关键调用链、学习与截图。 |
| 新增 | `docs/development/phase2b1-review/FILES.md` | 本轮逐文件增改清单（此文件）。 |
| 新增 | `docs/development/phase2b1-review/UNTRACKED-SOURCES.txt` | 本轮新增源码与最终跟踪状态说明。 |
| 新增 | `docs/development/results/phase2b1-workflow-smoke.json` | 15项真实TCP检查，仅合成聚合记录。 |
| 新增 | `docs/development/results/phase2b1/archive.xml` | 脱敏实际测试回执；数量、结果及检查点见开发记录。 |
| 新增 | `docs/development/results/phase2b1/browser.json` | 脱敏实际测试回执；数量、结果及检查点见开发记录。 |
| 新增 | `docs/development/results/phase2b1/final-display.xml` | 脱敏实际测试回执；数量、结果及检查点见开发记录。 |
| 新增 | `docs/development/results/phase2b1/final-ui.xml` | 脱敏实际测试回执；数量、结果及检查点见开发记录。 |
| 新增 | `docs/development/results/phase2b1/full.xml` | 脱敏实际测试回执；数量、结果及检查点见开发记录。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-analysis-bands.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-analysis-overview.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-analysis-rates.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-analysis-table.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-conflict.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-export-placeholder.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-home.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-review.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-scope-change.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/phase2b1-writing-placeholder.png` | 真实Edge合成工作流截图。 |
| 新增 | `docs/development/results/phase2b1/views.xml` | 脱敏实际测试回执；数量、结果及检查点见开发记录。 |
| 修改 | `scripts/smoke_dashboard.py` | 独立TCP/Streamlit回归；显式播种历史测试夹具、验证新确认流程。 |
| 修改 | `src/copilot/api.py` | 生产app不自动播种；保留工厂兼容选项。 |
| 修改 | `src/copilot/dashboard/app.py` | 三视图复用、中文展示、折叠证据、保留独立旧Agent组件。 |
| 修改 | `src/copilot/dashboard/client.py` | 类型化health和新增后端错误的友好显示。 |
| 新增 | `src/copilot/dashboard/example.py` | 只在显式请求时生成6名虚构学生的同格式XLSX。 |
| 修改 | `src/copilot/dashboard/import_panel.py` | 上传/核对、问题分级、人工确认、5MiB控件限制与本地记录入口。 |
| 修改 | `src/copilot/dashboard/presentation.py` | 仅展示投影；中文标签、图表排版、同名课程选项区分。 |
| 新增 | `src/copilot/dashboard/workbench.py` | 五步导航、条件筛选、内容选择、未来步骤占位。 |
| 新增 | `src/copilot/dashboard/workflow.py` | 纯状态模块：确认/范围/版本失效、导航和聚合ReportContext。 |
| 修改 | `src/copilot/data/college.py` | 提交结果增加私有档案ID和complete/pending状态。 |
| 修改 | `src/copilot/data/college_service.py` | 空库预览及确认时原件准备、事务提交、归档完成协调。 |
| 新增 | `src/copilot/data/import_archive.py` | 私有原件、脱敏清洗记录、确认回执；校验清理路径。 |
| 修改 | `src/copilot/storage.py` | 在现有写事务内检查expected_empty，防止旧空库预览覆盖。 |
| 修改 | `tests/test_college_dashboard.py` | 保留原4项，适配明确核对和确认步骤。 |
| 修改 | `tests/test_dashboard.py` | 保留原23项，适配health首页与独立Agent组件。 |
| 新增 | `tests/test_import_archive.py` | 新增7项私有留存、失败次序、pending与安全回执检查。 |
| 新增 | `tests/test_workflow_empty.py` | 新增7项空库启动、首次导入与并发保护检查。 |
| 新增 | `tests/test_workflow_state.py` | 新增25项纯状态、失效边界与聚合上下文检查。 |
| 新增 | `tests/test_workflow_ui.py` | 新增15项工作人员流程验收，含最后补充的同名课程选择。 |
