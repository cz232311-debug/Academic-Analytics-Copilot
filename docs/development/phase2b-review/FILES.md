# Phase 2B 逐文件变更清单

相对基线 `01dbba25ea44f2032bffd997c1bff48c90b68e91`。A=新增，M=修改。无删除文件。下面包含全部已实现改动和本次材料准备，既有Phase2A改动明确单列职责。

| 状态 | 文件 | 职责 |
|---|---|---|
| M | `.env.example` | 环境变量示例，不含实际Key |
| A | `AGENTS.md` | 后续每轮开发、验证、提交与审查材料的仓库规则 |
| M | `README.md` | 英文项目入口与功能状态；包含此前Phase2A状态说明 |
| A | `START_HERE.zh-CN.md` | 电脑重启后启动两个服务与配置模型的指令 |
| M | `docs/development/02a-deepseek.md` | 此前Phase2A真实调用与修复记录 |
| A | `docs/development/02b-workbench.md` | Phase2B进度、设计、验收、问题及剩余能力 |
| A | `docs/development/phase2b-review/FILES.md` | 本文逐文件变更清单 |
| A | `docs/development/phase2b-review/LEARNING.zh-CN.md` | 三个代码片段、调用链、知识点和独立审查建议 |
| A | `docs/development/phase2b-review/untracked-source-files.txt` | 补记提交前未跟踪源码快照及提交后的状态 |
| M | `docs/development/results/14-deepseek-offline-demo.json` | 用户原先已有的未提交离线结果，仅保存单独差异，不提交 |
| A | `docs/development/results/phase2b-core.xml` | 已执行196项后端里程碑JUnit结果 |
| A | `docs/development/results/phase2b-http-smoke.json` | 上一轮实际15项HTTP验收及合成聚合样例 |
| A | `docs/development/results/phase2b-pytest.xml` | 上一轮完整230项JUnit结果 |
| A | `docs/development/results/phase2b-review-pytest.xml` | 本次重新执行230项测试的JUnit结果 |
| M | `pyproject.toml` | 可选工作台依赖声明 |
| M | `requirements.lock.txt` | 本机实测依赖版本 |
| M | `scripts/smoke_agent.py` | 显式固定旧版Agent验收数据，使用本地模型桩 |
| A | `scripts/smoke_dashboard.py` | 隔离服务、真实HTTP和页面行为验收 |
| M | `scripts/smoke_http.py` | 显式固定旧版HTTP验收数据，兼容新bootstrap |
| A | `scripts/start_backend.ps1` | 后端启动、Mock/DeepSeek选择与隐藏Key输入 |
| A | `scripts/start_dashboard.ps1` | 前端启动与后端地址配置 |
| M | `src/copilot/agent/provider.py` | 此前Phase2A的DeepSeek index兼容与安全错误诊断修复 |
| M | `src/copilot/agent/tools.py` | 保持原Agent工具目录边界，不发送新增报表元数据 |
| M | `src/copilot/analytics.py` | 复用统计核心生成课程报告与精确均分分层 |
| M | `src/copilot/api.py` | 课程报告和筛选目录HTTP接口 |
| A | `src/copilot/dashboard/__init__.py` | 工作台包入口 |
| A | `src/copilot/dashboard/app.py` | 筛选、三种视图、Agent表单与证据展示 |
| A | `src/copilot/dashboard/client.py` | 类型化HTTP客户端、超时和友好错误 |
| A | `src/copilot/dashboard/presentation.py` | 只投影后端结果的表格、图表与CSV |
| M | `src/copilot/data/generate.py` | 不改变成绩随机序列的合成分类扩展 |
| M | `src/copilot/data/importing.py` | 兼容旧文件并接受声明的可选列 |
| M | `src/copilot/models.py` | 专业/类型枚举与兼容旧数据的指纹载荷 |
| M | `src/copilot/schema.sql` | 两项可选分类字段及数据库约束 |
| M | `src/copilot/schemas.py` | 课程报告、筛选和展示口径的Pydantic契约 |
| M | `src/copilot/storage.py` | 旧库迁移、绑定参数筛选、版本化快照和目录 |
| A | `streamlit_app.py` | Streamlit运行入口 |
| A | `tests/test_course_reports.py` | 独立期望数值、分类边界、迁移和导入兼容测试 |
| A | `tests/test_dashboard.py` | 客户端、展示、CSV和离线页面测试 |
| A | `tests/test_dashboard_http.py` | 真实TCP后端与页面联调测试 |
| M | `tests/test_deepseek_narrative.py` | 此前Phase2A的协议修复回归测试 |

本次材料准备没有改动业务逻辑。先前开发代码本轮仅纳入分组提交。完整提交覆盖范围见导出包 README；剩余用户离线结果不混入功能提交。
