# issue-radar

定时抓取 GitHub issue，读取正文与评论，并分两层判断：
- 规则层判断认领状态：`claimed / maybe_claimed / open`
- AI 只判断难度、类别、适配度和推荐指数
- 只对新 issue 做 AI 分析，避免重复调用模型

当前默认只监控 `llvm/llvm-project`。
除了 `good first issue` 之外，也会补充抓取 `docs/tests/cleanup/refactor/typo/comment` 等标签或关键词信号，扩大低门槛 issue 覆盖面。

## 能力
- 通过 GitHub API 抓取 issue、评论、timeline 事件
- 在同一仓库内支持多条查询，并按 issue 去重合并
- 输出原始 JSON 到 `data/raw/issues.json`
- 调用 AI 输出分析结果到 `data/enriched/issues.analyzed.json`
- 维护 `data/state/analyzed_issues.json`，只分析新 issue
- 结合你的画像生成推荐指数
- 去重后为高分 issue 生成邮件通知
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
- 通知阈值

### `config/profile.yaml`
- 熟悉语言
- 可接受方向
- 偏好难度
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
1. 抓取 issue
2. 规则层识别 `claimed / maybe_claimed / open`
3. 只对新 issue 进行 AI 分析
4. `claimed` 直接跳过 AI
5. 同一 issue 被多个查询命中时会合并 `matched_queries/source_signals`
5. 判断是否需要通知
6. 如有需要，发送邮件
7. 更新 `data/state/notified_issues.json` 和 `data/state/analyzed_issues.json`
