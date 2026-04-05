# issue-radar

定时抓取 GitHub issue，读取正文与评论，并分两层判断：
- 规则层判断认领状态：`claimed / open`
- AI 只判断难度、类别、适配度与问题摘要
- 只对最近 14 天内创建、且首次看到的新 issue 做 AI 分析，避免重复调用模型

当前默认只监控 `llvm/llvm-project`。
除了 `good first issue` 之外，也会补充抓取 `docs/tests/cleanup/refactor/typo/comment` 等标签或关键词信号，扩大低门槛 issue 覆盖面。
当前配置也会额外关注 `clang-format` 与 `CIR/ClangIR` 相关 issue。
如果 issue 已经有非机器人、且不是 issue 创建者本人的评论，则直接视为已有人跟进。

## 能力
- 通过 GitHub API 抓取 issue、评论、timeline 事件
- 在同一仓库内支持多条查询，并按 issue 去重合并
- 输出原始 JSON 到 `data/raw/issues.json`
- 调用 AI 输出分析结果到 `data/enriched/issues.analyzed.json`
- 维护 `data/state/analyzed_issues.json`，只分析新 issue
- 结合你的画像判断适配度与难度
- 去重后为匹配条件的 issue 生成邮件通知，同时输出纯文本和 HTML 正文，内容包含 issue 创建时间和原始 labels
- 支持 GitHub Actions 在每小时 `07`、`37` 分执行一次
- `claimed` issue 只写状态、不进入分析结果
- AI 调用失败时只重试 1 次，仍失败则直接丢弃，不生成保底分析

## 本地运行
```bash
python3 -m pip install -e .
python3 scripts/fetch_issues.py
python3 scripts/analyze_issues.py
python3 scripts/notify.py
```

## 监控控制
```bash
python3 scripts/control_monitor.py status
python3 scripts/control_monitor.py pause
python3 scripts/control_monitor.py resume
```

- `pause` 会完全冻结监控：不抓取、不分析、不通知，也不更新现有 state 文件
- `resume` 后从恢复后的正常轮询继续，不补扫暂停期间错过的 issue
- 暂停或恢复后，需要把 `data/state/monitor_control.json` 提交到仓库，GitHub Actions 才会按新状态执行

## 环境变量
### GitHub
- `GITHUB_TOKEN` 或 `GH_TOKEN`

### AI
- `AI_BASE_URL`
- `AI_API_KEY`
- `AI_MODEL`
- `AI_TIMEOUT_SECONDS`，默认 `60`
- `AI_MAX_WORKERS`，默认 `3`

默认实现按 OpenAI-compatible 接口调用，第一版建议接 Qwen。

## 配置
### `config/repos.yaml`
- 监控仓库
- GitHub 搜索条件
- 每条查询的 `source_signals`
- 抓取上限
- `max_issue_age_days`，默认只处理最近 14 天创建的 issue

### `data/state/monitor_control.json`
- `status`：`running / paused`
- `paused_at / resumed_at / updated_at`：状态变更时间戳

### `config/profile.yaml`
- 熟悉语言
- 可接受方向
- 偏好难度
- 偏好的任务风格
- 不想做的内容

## GitHub Actions
工作流文件是 `.github/workflows/issue-radar.yml`。

需要配置这些 secrets：
- `AI_BASE_URL`
- `AI_API_KEY`
- `AI_MODEL`
- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `NOTIFY_TO`
- `NOTIFY_FROM`

工作流会：
1. 读取 `data/state/monitor_control.json`，判断当前是否暂停
2. 若未暂停，抓取 issue
3. 跳过超过 `max_issue_age_days` 的旧 issue
4. 规则层识别 `claimed / open`
5. 只对新 issue 进行 AI 分析
6. `claimed` 直接跳过 AI
7. 同一 issue 被多个查询命中时会合并 `matched_queries/source_signals`
8. 判断是否需要通知
9. 如有需要，发送邮件
10. 更新 `data/state/notified_issues.json` 和 `data/state/analyzed_issues.json`
