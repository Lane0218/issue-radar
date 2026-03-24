# issue-radar

定时抓取 GitHub issue，读取正文与评论，调用兼容 OpenAI 协议的模型分析：
- 这个 issue 有没有明显被别人接手
- 难度和类别是否适合你
- 是否值得发通知

第一版默认监控 `llvm/llvm-project` 的 `good first issue`。

## 能力
- 通过 GitHub API 抓取 issue、评论、timeline 事件
- 输出原始 JSON 到 `data/raw/issues.json`
- 调用 AI 输出分析结果到 `data/enriched/issues.analyzed.json`
- 结合你的画像生成推荐指数
- 去重后为高分 issue 生成邮件通知
- 支持 GitHub Actions 每 30 分钟执行一次

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

默认实现按 OpenAI-compatible 接口调用，第一版建议接 Qwen。

## 配置
### `config/repos.yaml`
- 监控仓库
- GitHub 搜索条件
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
2. 调用 AI 分析
3. 判断是否需要通知
4. 如有需要，发送邮件
5. 更新 `data/state/notified_issues.json`，避免重复通知
