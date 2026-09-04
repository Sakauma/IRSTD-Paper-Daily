# IRSTD Paper Daily 使用说明

本项目每天从 arXiv 搜索红外小目标检测（IRSTD）相关论文。历史数据收录从
2025-01-01 首次提交到 arXiv 的全部高相关论文，不限制总数量，并维护论文
JSON、增量状态和三种输出：

- 根目录 `README.md`：GitHub 仓库主页表格。
- `docs/index.md`：可选的 GitHub Pages 页面。
- `docs/wechat.md`：沿用 `cv-arxiv-daily` 的微信版条目格式，便于复制到微信文章或群消息。

## Usage

### 快速开始

1. 修改根目录 `config.yaml` 中的 `user_name` 和 `repo_name`。
2. 在 GitHub 仓库 Settings → Actions → General 中，将 Workflow permissions 设置为 **Read and write permissions**。
3. 在 Actions 页面手动运行 **Update IRSTD Paper Daily**。
4. 可选：在 Settings → Pages 中选择 `main` 分支的 `/docs` 目录发布 GitHub Pages。

工作流会使用仓库自带的 `GITHUB_TOKEN` 查询 GitHub 代码仓库并提交生成文件。未找到代码仓库的论文保留为 `null`。

## 本地运行

```bash
python -m pip install -r requirements.txt
python daily_arxiv.py
```

首次运行会从 `start_date` 开始执行全量抓取，并把成功日期写入
`docs/irstd-paper-daily-state.json`。后续每日运行只查询上次成功日期附近的增量
窗口；默认向前回看 3 天，以容忍 arXiv 收录延迟。历史论文仍保存在主 JSON 中，
不会因增量查询而删除。

程序优先采用论文作者写在 arXiv 摘要或备注中的 GitHub 地址，再对 GitHub 搜索
结果逐个校验。GitHub Search API 未认证时限流较低，建议在 Actions 中执行代码
链接补查。

要忽略增量水位并重新扫描从 `start_date` 至今的全部论文：

```bash
python daily_arxiv.py --full-refresh
```

要为历史论文补齐代码链接：

```bash
python daily_arxiv.py --backfill_code
```

已有代码链接的论文会跳过，不会重复查询。

每周工作流会组合执行以下命令，同时刷新旧论文元数据和缺失代码链接：

```bash
python daily_arxiv.py --full-refresh --backfill_code
```

## 配置新领域

在 `config.yaml` 的 `domains` 下增加条目即可：

```yaml
domains:
    "New Domain":
        enable: true
        max_results: null
        start_date: "2025-01-01"
        incremental_lookback_days: 3
        filters:
            - "keyword1"
            - "keyword phrase 2"
```

`max_results: null` 表示抓取日期范围内的全部结果；正整数表示只取最新的指定
数量。`start_date` 是全量收录的起始日期；`incremental_lookback_days` 是每日
查询相对于上次成功日期向前回看的天数。`enable: false` 会停止抓取该领域的新
论文，但不会删除历史数据。含空格的过滤词按完整短语搜索，多个过滤词以 `OR`
连接。

GitHub Actions 每天北京时间 08:00 执行增量更新，每周一北京时间 16:00 执行
全量论文刷新和历史代码链接补查。

默认 IRSTD 关键词只保留明确包含 infrared、IRSTD 或 SIRST 的检索词，避免把
声呐小目标、普通 UAV 检测等论文误收进列表。

## 微信版输出说明

`publish_wechat: true` 时，程序会更新：

- `docs/irstd-paper-daily-wechat.json`：微信版条目的结构化索引。
- `docs/wechat.md`：按领域分组的项目符号列表，包含论文和代码链接。

该功能生成可复制的微信版日报，不直接调用微信 API，也不会发送消息。若需要自动发送，应在仓库外另行配置合规的企业微信/微信公众号服务。

## 测试

```bash
python tests/test_smoke.py
```

测试不访问网络，覆盖配置解析、增量与全量日期窗口、数据合并、Markdown 渲染、
微信渲染以及代码链接校验的基本行为。

## 参考项目

本项目第一版主要参考 [Fortuneteller6/IRSTD-Arxiv-Daily](https://github.com/Fortuneteller6/IRSTD-Arxiv-Daily)，微信版输出参考 [Vincentqyw/cv-arxiv-daily](https://github.com/Vincentqyw/cv-arxiv-daily)。本仓库保留 Apache License 2.0 许可文件。
