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

GitHub Actions 每天北京时间 08:00 执行增量更新。每周一北京时间 16:00 执行
全量论文刷新和历史代码链接补查。两个工作流都会在论文目录实际变化时发送通知。

默认 IRSTD 关键词只保留明确包含 infrared、IRSTD 或 SIRST 的检索词，避免把
声呐小目标、普通 UAV 检测等论文误收进列表。

## 微信通知配置

自动化工作流使用 [Server酱](https://sct.ftqq.com/) 将论文变化推送到绑定的微信。
推荐使用 Server酱 Turbo；程序也兼容 [Server酱³](https://sc3.ft07.com/)。配置步骤
如下：

1. 登录 Server酱，在后台按照提示绑定用于接收消息的微信服务号。
2. 打开 SendKey 页面，复制以 `SCT` 开头的 Turbo SendKey；使用 Server酱³ 时，
   复制以 `sctp` 开头的 SendKey。SendKey 相当于密码，不要写入仓库文件。
3. 打开 GitHub 仓库的 **Settings → Secrets and variables → Actions**，新建一个
   Repository secret：
   - Name：`SERVERCHAN_SENDKEY`
   - Secret：上一步复制的完整 SendKey
4. 在 GitHub Actions 页面手动运行 **Update IRSTD Paper Daily** 测试推送。

接收微信由 Server酱后台管理，因此不需要在仓库中配置 UID。迁移完成后，可以在
GitHub Actions Secrets 中删除不再使用的 `WXPUSHER_APP_TOKEN` 和
`WXPUSHER_UIDS`。

首次成功的 Server酱通知会发送当前完整论文目录，不受增量通知展示数量限制。
程序按 UTF-8 字节数把单条消息控制在 28 KB 以内，超出时按论文从新到旧拆分，
最多发送 5 条；如果 5 条仍然放不下，最老的部分不再展示，末条消息会提示省略
数量并提供完整目录链接。程序随后会在 `docs/irstd-paper-daily-state.json` 中记录
`serverchan` 初始化状态。旧通知服务的初始化记录不会跳过这次首次全量发送。
后续只有目录实际变化时才发送，内容包括新增论文，以及标题、作者、arXiv 信息
或代码链接发生变化的论文。每日和每周工作流都没有发现变化时，不会发送“今日
无新增”。

后续增量通知始终只发送一条，默认最多展示 20 篇变化；可通过 `config.yaml` 的
`wechat_notification.max_papers` 修改。消息同样按 UTF-8 字节数限制在 28 KB 内，
极端情况下会安全截断。这里的代码更新指目录中的代码链接从空值变为 GitHub 地址
或链接发生变化，不监控代码仓库内部的每次 commit。

没有配置 `SERVERCHAN_SENDKEY` 时，程序会安全跳过通知，并保持“首次未发送”状态；
配置完成后的下一次运行仍会发送完整目录。SendKey 只从 GitHub Secret 读取，不应
写入 `config.yaml` 或提交到仓库。

本地测试通知可先设置同名环境变量，再运行：

```bash
python daily_arxiv.py --notify-wechat
```

## 微信版输出说明

`publish_wechat: true` 时，程序会更新：

- `docs/irstd-paper-daily-wechat.json`：微信版条目的结构化索引。
- `docs/wechat.md`：按领域分组的项目符号列表，包含论文和代码链接。

微信版文件与 Server酱通知互相独立：前者是可复制的完整 Markdown 日报，后者是
自动化工作流发送的变化摘要。

## 测试

```bash
python tests/test_smoke.py
```

测试不访问网络，覆盖配置解析、增量与全量日期窗口、数据合并、Markdown 渲染、
微信渲染、Server酱通知以及代码链接校验的基本行为。

## 参考项目

本项目第一版主要参考 [Fortuneteller6/IRSTD-Arxiv-Daily](https://github.com/Fortuneteller6/IRSTD-Arxiv-Daily)，微信版输出参考 [Vincentqyw/cv-arxiv-daily](https://github.com/Vincentqyw/cv-arxiv-daily)。本仓库保留 Apache License 2.0 许可文件。
