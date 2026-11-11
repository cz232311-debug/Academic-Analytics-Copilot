# UX增量文件清单

基线：`268e094b29c49166af10c355d9c1893959f3943f`。完整检查点由审查ZIP manifest固定。

没有删除文件，未修改数据库、AnalyticsEngine、Adapter、Agent核心、依赖锁或历史测试回执。用户原有离线回执不属于下表。[实现与验证记录](../02b-ux-usability.md)。

| 变化 | 文件 | 责任 |
|---|---|---|
| 新增 | `.streamlit/config.toml` | 原生18px字号、标题层级、深浅主题背景与文字配色。 |
| 修改 | `README.md` | 当前UX入口、统一ZIP与本轮验证边界。 |
| 修改 | `START_HERE.zh-CN.md` | 新的页面操作与主题加载、下载方式。 |
| 新增 | `docs/development/02b-ux-usability.md` | 问题优先级、处理对照、调用链、学习记录、测试与真实截图。 |
| 修改 | `docs/development/02b-workbench.md` | 阶段进展索引，链接当前UX增量。 |
| 修改 | `docs/development/02b1-workflow.md` | 保留历史并标明后续UX替换入口。 |
| 新增 | `docs/development/results/ux-usability/audit-export.xml` | 本轮成功执行的脱敏测试/浏览器回执。 |
| 新增 | `docs/development/results/ux-usability/browser-after.json` | 本轮成功执行的脱敏测试/浏览器回执。 |
| 新增 | `docs/development/results/ux-usability/browser-before.json` | 本轮成功执行的脱敏测试/浏览器回执。 |
| 新增 | `docs/development/results/ux-usability/full.xml` | 本轮成功执行的脱敏测试/浏览器回执。 |
| 新增 | `docs/development/results/ux-usability/http-smoke.json` | 本轮成功执行的脱敏测试/浏览器回执。 |
| 新增 | `docs/development/results/ux-usability/ui.xml` | 本轮成功执行的脱敏测试/浏览器回执。 |
| 新增 | `docs/development/results/ux-usability/ux-after-analysis-dark-desktop.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-analysis-dark-narrow.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-analysis-dark-zoom125-equivalent.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-analysis-light-desktop.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-analysis-light-narrow.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-analysis-light-zoom125-equivalent.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-bands-dark.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-bands-light.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-chart-dark-desktop.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-chart-dark-narrow.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-chart-dark-zoom125-equivalent.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-chart-light-desktop.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-chart-light-narrow.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-chart-light-zoom125-equivalent.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-conflict-dark.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-conflict-light.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-download-dark.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-download-light.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-home-dark-desktop.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-home-dark-narrow.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-home-dark-zoom125-equivalent.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-home-light-desktop.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-home-light-narrow.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-home-light-zoom125-equivalent.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-long-name-dark.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-long-name-light.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-review-dark.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-review-light.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-scope-change-dark.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-after-scope-change-light.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-before-analysis-dark.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-before-analysis-light.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-before-chart-dark.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-before-chart-light.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-before-home-dark.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/results/ux-usability/ux-before-home-light.png` | 真实Edge合成页面截图，非设计稿。 |
| 新增 | `docs/development/ux-review/FILES.md` | 本轮完整逐文件增改清单（此文件）。 |
| 新增 | `docs/development/ux-review/UNTRACKED-SOURCES.txt` | 本轮新增源码/配置/测试及最终跟踪状态。 |
| 修改 | `src/copilot/dashboard/app.py` | 正文说明、成绩表三模式、逐课程均分行和通俗指标解释。 |
| 新增 | `src/copilot/dashboard/audit_export.py` | 当前有效聚合报告的内存ZIP；清洗白名单、CSV保护与一致性证据。 |
| 修改 | `src/copilot/dashboard/client.py` | 常见连接、超时、版本、结构错误的中文恢复提示；错误码保留。 |
| 修改 | `src/copilot/dashboard/import_panel.py` | 精简上传核对说明、问题解决提示、业务课程清单，保留本地记录而移除分散JSON。 |
| 修改 | `src/copilot/dashboard/presentation.py` | 高对比图表和字号；后端均分分组逐行投影，不重算。 |
| 修改 | `src/copilot/dashboard/workbench.py` | 仅前三步导航、状态与成功引导、折叠范围和后续设置、统一下载。 |
| 新增 | `tests/test_audit_export.py` | 新增13项导出/隐私/版本/范围/空值检查。 |
| 修改 | `tests/test_college_dashboard.py` | 保留原4项；范围说明由caption变正文的断言适配。 |
| 修改 | `tests/test_dashboard.py` | 保留原23项；中文连接与错误提示断言适配，保留独立Agent组件回归。 |
| 修改 | `tests/test_workflow_ui.py` | 保留原流程断言并补3项UI合同，校验无JSON/统一ZIP/表格/长名称/失效。 |
