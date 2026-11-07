# 在本机启动 Academic Analytics Copilot

本项目使用完全合成的学生数据，尚未在学院正式部署。VS Code 是可选的代码编辑器，启动系统只需要 PowerShell 和浏览器。

## 第一次安装，或本轮更新后安装新增依赖

在项目目录打开 PowerShell，依次执行。已经存在 `.venv` 时跳过创建环境的第一条 Python 命令。

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --only-binary=:all: -r requirements.lock.txt
& '.\.venv\Scripts\python.exe' -m pip install --no-deps --no-build-isolation -e .
& '.\.venv\Scripts\python.exe' -m pip check
```

使用 Python 3.13。锁定文件包含本轮实际运行的 Pandas 2.3.3、Streamlit 和 Altair。虚拟环境目录仍存在时，电脑重启后不需要重新安装。

## 每次电脑重启后的标准启动

需要两个 PowerShell 窗口。它们运行两个服务，请保持窗口打开。

**窗口 A，启动 FastAPI 后端**

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
.\scripts\start_backend.ps1
```

默认使用 Mock。统计数据由真实 SQLite/Pandas 计算，AI 选择工具与回答流程是离线模拟，不会产生模型费用。

看到 `Uvicorn running on http://127.0.0.1:8000` 后，后端已启动。

**窗口 B，启动 Streamlit 页面**

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
.\scripts\start_dashboard.ps1
```

在浏览器打开 [学业分析工作台](http://127.0.0.1:8501)。8501 是用户页面端口，8000 是后端 API 端口。

如果 PowerShell 阻止运行 `.ps1`，使用下面的等效手动命令即可，无需修改系统执行策略。

窗口 A

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
$env:ACADEMIC_DB_PATH = 'data/workbench-demo.sqlite3'
$env:COPILOT_LLM_PROVIDER = 'mock'
& '.\.venv\Scripts\python.exe' -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000
```

窗口 B

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
$env:COPILOT_API_BASE_URL = 'http://127.0.0.1:8000'
& '.\.venv\Scripts\python.exe' -m streamlit run streamlit_app.py --server.address=127.0.0.1 --server.port=8501 --server.headless=true --browser.gatherUsageStats=false
```

`data/workbench-demo.sqlite3` 第一次启动时自动生成。它包含 1,382 名虚构学生、40 个班级、96 门课程，以及合成的专业、课程类型。后续启动复用数据库。原来的 `data/agent-demo.sqlite3` 保留。

## 在页面中验证三种业务视图

1. 在左侧选 `Grade 1`，或在 `Course` 选择 `G1-C01 · Mathematics`，点击 `Load analysis`。
2. `Course analysis` 显示课程分段表，可下载 CSV。
3. `Rates and samples` 显示及格率和不及格率横向柱状图。悬停可查看人数与有效样本量。
4. `Course mean bands` 按课程精确均分分层。它与学生成绩分段是两种不同统计。
5. `Report preview` 显示统一报告结构、统计范围和规则，目前不导出 Word/PDF。

单独选择 G1-C01、其余条件选 All、学期选 2026-T1 时，应看到以下结果。

| 指标 | 结果 |
|---|---:|
| 有效成绩记录 | 469 |
| 平均分 | 69.73 |
| 及格率 / 不及格率 | 67.80% / 32.20% |
| 优良率，分数 ≥80 | 29.00% |
| 分段人数，低到高 | 151 / 79 / 103 / 85 / 51 |
| 课程均分所属区间 | 60–<70 |

表格、图表、CSV 来自同一个后端报告与数据版本。专业、年级等条件同时生效，互相矛盾的选择可能没有数据。

## 验证 AI Copilot

页面底部保留默认英文问题，点击 `Ask Copilot`。

```text
What is the average score and pass rate for G1-C01?
```

Mock 模式应显示 69.73、67.80% 和 Evidence r1。展开证据与 Trace 可查看实际工具参数、数据版本和调用信息。Mock 没有真实 Token Usage。

AI 不自动继承左侧筛选。现有四个工具只支持学期、年级、课程、班级等既有能力，不支持专业或课程类型筛选。请核对回答自己的 Evidence 范围。中文、个人名单和敏感群体查询目前不支持。

## 可选，切换到真实 DeepSeek

先在窗口 A 按 `Ctrl+C` 停止旧后端，然后执行。

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
.\scripts\start_backend.ps1 -Provider deepseek
```

脚本在当前窗口没有 Key 时显示隐藏输入提示。此时粘贴 Key 并回车，Key 不会显示，也不写入项目文件。环境变量不会因电脑重启而自动保留，下次会再次提示。Streamlit 窗口不需要 Key。

无法运行脚本时，窗口 A 使用以下命令。

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
$env:ACADEMIC_DB_PATH = 'data/workbench-demo.sqlite3'
$env:COPILOT_LLM_PROVIDER = 'deepseek'
$env:COPILOT_LLM_BASE_URL = 'https://api.deepseek.com'
$env:COPILOT_LLM_MODEL = 'deepseek-flash'
$copilotSecret = Read-Host 'Paste the DeepSeek API key (hidden)' -AsSecureString
$env:COPILOT_LLM_API_KEY = [System.Net.NetworkCredential]::new('', $copilotSecret).Password
Remove-Variable copilotSecret
& '.\.venv\Scripts\python.exe' -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000
```

刷新工作台，确认 Provider 显示 deepseek。提交问题前勾选外部 API 调用授权框，然后点击 `Ask Copilot`。这会发生真实付费调用。打开页面、筛选统计、查询状态均不会调用模型。

本轮没有代你执行付费调用。此前 Phase 2A 的真实测试，与本轮 Mock 页面联调是两份不同验收记录。

## 常见问题与停止方式

- 页面显示 `Cannot reach FastAPI`，先检查窗口 A 是否仍在运行，浏览器访问 [健康接口](http://127.0.0.1:8000/health)。
- 提示端口占用，找到之前启动服务的窗口，按 `Ctrl+C`。避免同时运行两个占用 8000 的后端。
- 看到旧接口或响应结构错误，停止旧后端后重新启动本轮代码，再刷新页面。
- 访问 8000 根路径出现 404 是正常的。用户页面在 8501，开发接口页面在 [Swagger UI](http://127.0.0.1:8000/docs)。
- 旧数据库的专业或类型显示 `Not provided` 是保留原信息的结果。不要把猜测分类写回旧数据。使用上述新 workbench 数据库可体验合成分类筛选。
- 修改筛选后需点 `Load analysis`。报告标题展示已实际计算的范围。
- 停止系统时，在两个服务窗口分别按 `Ctrl+C`。关闭浏览器标签页不会停止服务。

## 复跑自动化验收

另开一个 PowerShell 窗口，在项目根目录执行。测试使用自己的临时数据库、端口和 Mock Provider，不影响已经启动的演示服务。

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
& '.\.venv\Scripts\python.exe' -m pytest -q --basetemp=.test-tmp-manual
& '.\.venv\Scripts\python.exe' scripts/smoke_dashboard.py
```

完整修改、口径、测试结果和剩余差距见 [Phase 2B 开发记录](docs/development/02b-workbench.md)。
