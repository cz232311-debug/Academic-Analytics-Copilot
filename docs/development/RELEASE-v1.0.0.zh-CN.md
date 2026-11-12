# v1.0.0 发布准备记录（GitHub 交付）

## 范围与边界

- 起始提交：`e4674c2`（Phase 3C 诊断验证记录），当时工作区有 Phase 3D 未提交改动。
- 本记录对应的提交为发布准备提交本身；按 AGENTS.md 约定，review 类产物不递归包含其自身提交。
- 性质：发布/整理轮次，无业务逻辑改动。

## 本轮文件变更

| 文件 | 类型 | 责任 |
| --- | --- | --- |
| `.gitignore` | 修改 | 排除 `.workbuddy/`（本地代理工作区）与 `docs/development/*.zip`（与 review 文件夹内容重复的二进制包） |
| `LICENSE` | 新增 | MIT 协议，版权人为 GitHub 账号 `cz232311-debug`（可改为真实姓名） |
| 本文件 | 新增 | 发布过程记录 |

上一提交（Phase 3D 增量）包含：`src/copilot/agent/semantics.py`、`src/copilot/dashboard/chat.py`、Phase 3D 五个验收/评估脚本、`tests/test_phase3d_*.py`、`docs/development/phase3d-review/` 评审包等。

## 实际验证记录

- 命令：`.venv/Scripts/python.exe -m pytest tests -q --basetemp=".test-tmp-pytest/full"`
- 结果：**611 passed, 1 warning**（已知 TestClient/Starlette 弃用警告），耗时 188s。
- 环境发现：Windows 中文用户名导致系统 Temp 路径 `C:\Users\星空\AppData\Local\Temp` 在 pytest 创建编号目录时报 `PermissionError [WinError 5]`；用 `--basetemp` 指向项目内目录后全部通过。复现者如遇同样问题可加该参数。
- 密钥扫描：对全部已跟踪文件及本次新增 JSON 结果文件执行 `sk-`/`Bearer`/`authorization` 模式扫描，无命中。
- 未进入仓库的本地内容：`data/*.sqlite3*`、`data/demo/`、`.env*`（保留 `.env.example`）、`.workbuddy/`、review zip、`__pycache__/` 等（均由 `.gitignore` 覆盖）。

## 发布动作

1. Phase 3D 增量提交于 `feature/chat-first-agent`。
2. 本发布准备提交后，`main` 快进合并至 feature 分支头部，并打 `v1.0.0` 标签。
3. 推送 `main`、`feature/chat-first-agent` 与 `v1.0.0` 至 `https://github.com/cz232311-debug/Academic-Analytics-Copilot`。

## 未验证/遗留事项

- 真实 DeepSeek 付费验收仍未授权执行；`docs/development/phase3d-review/PROTOCOL_ACCEPTANCE.zh-CN.md` 中的 live 计划保持"未授权"状态。
- `pyproject.toml` 版本仍为 `0.2.0`；`v1.0.0` 为仓库发布标签，未改动包版本（避免无必要的业务面变更）。
