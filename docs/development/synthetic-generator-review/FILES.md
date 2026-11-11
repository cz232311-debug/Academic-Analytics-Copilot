# 合成生成模块文件清单

基线`9a5bc4b40f4c336ca33e5f4a9ed4868a33d0d80f`。最终边界由ZIP manifest固定。没有删除文件，没有改动统计引擎、Adapter解析器、数据库、Agent核心或依赖。用户原有离线回执不属于下表。[交付记录](../02b-synthetic-generator.md)。

| 变化 | 文件 | 责任 |
|---|---|---|
| 修改 | `README.md` | 当前可配置合成入口与使用边界。 |
| 修改 | `START_HERE.zh-CN.md` | 工作人员生成、换批、下载与核对操作。 |
| 新增 | `docs/development/02b-synthetic-generator.md` | 生成规则、状态边界、调用链、学习与实际证据。 |
| 修改 | `docs/development/02b-workbench.md` | 阶段索引。 |
| 新增 | `docs/development/results/synthetic-generator/browser.json` | 成功执行的脱敏实际验证回执。 |
| 新增 | `docs/development/results/synthetic-generator/full.xml` | 成功执行的脱敏实际验证回执。 |
| 新增 | `docs/development/results/synthetic-generator/generator.xml` | 成功执行的脱敏实际验证回执。 |
| 新增 | `docs/development/results/synthetic-generator/panel.xml` | 成功执行的脱敏实际验证回执。 |
| 新增 | `docs/development/results/synthetic-generator/stale-upload.xml` | 成功执行的脱敏实际验证回执。 |
| 新增 | `docs/development/results/synthetic-generator/synthetic-generator-analysis.png` | 真实Edge合成数据截图。 |
| 新增 | `docs/development/results/synthetic-generator/synthetic-generator-ready.png` | 真实Edge合成数据截图。 |
| 新增 | `docs/development/results/synthetic-generator/synthetic-generator-review.png` | 真实Edge合成数据截图。 |
| 新增 | `docs/development/results/synthetic-generator/synthetic-generator-settings.png` | 真实Edge合成数据截图。 |
| 新增 | `docs/development/results/synthetic-generator/ui.xml` | 成功执行的脱敏实际验证回执。 |
| 新增 | `docs/development/synthetic-generator-review/FILES.md` | 逐文件责任清单。 |
| 新增 | `docs/development/synthetic-generator-review/UNTRACKED-SOURCES.txt` | 新增源码与跟踪状态。 |
| 修改 | `src/copilot/dashboard/import_panel.py` | 接入生成器入口，保留旧固定示例。 |
| 新增 | `src/copilot/dashboard/synthetic_panel.py` | 首页按需打开生成模块、独立快照、换批与明确使用进入原核对流。 |
| 新增 | `src/copilot/data/synthetic_college.py` | 纯内存、可配置、可复现的学院宽表合成生成器，三个验证场景。 |
| 新增 | `tests/test_synthetic_college.py` | 新增12项源矩阵/Adapter/SQLite/AnalyticsEngine独立验证。 |
| 新增 | `tests/test_synthetic_panel.py` | 新增9项生成/下载/参数失效/确认/跨导航与旧控件值验收。 |
