# Daily Feishu Push

这个目录用于放到 GitHub 仓库中，通过 GitHub Actions 每天北京时间 11:00 自动推送三条消息：

- TapTap 评论日报
- Figma 项目更新日报
- Jira 今日任务日报

## 仓库 Secrets

在 GitHub 仓库里打开 `Settings` → `Secrets and variables` → `Actions` → `New repository secret`，添加这些 secrets：

- `FEISHU_TAPTAP_WEBHOOK`
- `FEISHU_TAPTAP_SECRET`
- `FEISHU_FIGMA_WEBHOOK`
- `FEISHU_FIGMA_SECRET`
- `FEISHU_JIRA_WEBHOOK`
- `FEISHU_JIRA_SECRET`
- `FIGMA_TOKEN`
- `JIRA_EMAIL`
- `JIRA_TOKEN`

## 文件位置

把本目录下的文件放进仓库后，最终结构应为：

```text
.github/workflows/daily-feishu-push.yml
scripts/daily_feishu_push.py
```

## 手动测试

文件和 secrets 都配置好后，可以在仓库的 `Actions` 页面里打开 `Daily Feishu Push`，点 `Run workflow` 立即测试一次。
