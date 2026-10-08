# IRSTD Paper Daily 使用说明

本项目从 arXiv 搜索红外小目标检测（IRSTD）相关论文，支持增量与全量更新。历史数据收录从
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

更新状态同时记录查询配置指纹与各领域缓存校验值。缓存不存在、为空、与状态不一致，
或修改了查询条件、起始日期、数量限制时，会自动执行全量重建。升级前的状态没有这些
字段，首次运行会全量刷新一次。JSON 使用临时文件加原子替换，写入失败保留旧文件。

增量窗口使用首次提交日期，因此旧论文的新版本由每周全量刷新补查；需要立即更新时
可手动执行 `--full-refresh`。正整数 `max_results` 只取最新的指定数量；如需完整收录，
请保持 `max_results: null`。

程序优先采用摘要或备注中明确标注为本文代码的地址，支持 GitHub、GitLab、GitCode、
Gitee 和 Anonymous 4open.science。提取时兼容换行和 Markdown 转义；同一代码声明中的
并列地址视为镜像，优先选用非匿名地址。基线引用和不同声明中不明确的多个候选仍会忽略。
GitHub 搜索结果必须在 README 中同时提供精确论文 ID 或完整标题，以及
实现说明；通用词重合不作为匹配依据。限流和临时服务错误会重试同一个请求，耗尽后
报错，不会把请求失败当成“没有代码”。GitHub Search API 未认证时限流较低，建议在
Actions 中执行代码链接补查。

要忽略增量水位并重新扫描从 `start_date` 至今的全部论文：

```bash
python daily_arxiv.py --full-refresh
```

要为历史论文补齐代码链接：

```bash
python daily_arxiv.py --backfill_code
```

回填也会重新核验历史搜索链接，并移除无法通过校验且未找到可靠替代项的链接。
记录中的 `code_source` 表示来源：`arxiv_metadata` 为明确的作者元数据，
`github_verified` 为通过核验的搜索结果，`legacy` 为尚未核验的旧缓存。
作者元数据和人工设置的 `code_source: manual` 不会被回填覆盖；人工确认的链接可用
`manual` 保留。抓取范围为 arXiv API 提供的摘要和备注；仅出现在论文正文中的代码地址
需要人工确认后写入 `code`，并标记 `code_source: manual`。例如 SANet（2610.09875）
按论文中的代码声明记录为 [GitCode](https://gitcode.com/m0_61988291/SANet)，论文同时
给出了 [匿名镜像](https://anonymous.4open.science/r/SANetE808/)。这些地址不会被同名
GitHub 搜索结果覆盖。网络故障会中止本次核验并保留磁盘上的原目录。

每周工作流会组合执行以下命令，同时刷新旧论文元数据和缺失代码链接：

```bash
python daily_arxiv.py --full-refresh --backfill_code
```

## 配置新领域

在 `config.yaml` 的 `domains` 下增加条目即可。简单领域可以使用 `filters`：

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

需要使用字段限定、括号和布尔条件时，可以直接配置原生 arXiv 查询；`query` 的
优先级高于 `filters`：

```yaml
domains:
    "New Domain":
        enable: true
        max_results: null
        query: '(all:infrared AND all:"small target") OR all:IRSTD'
```

增量工作流目前仅支持手动触发，每日定时触发已移除。每周一北京时间 16:37
定时触发全量论文刷新和历史代码链接补查。定时避开整点以降低拥堵概率，但 GitHub
不保证准时触发，高负载时可能延迟甚至丢弃定时任务；如需补跑，可在 Actions
页面手动运行对应工作流。

两个工作流共用 `irstd-paper-daily-publish` 并发组，同一时间只运行一个，后来的
任务等待且不取消正在执行的任务。排队结束后检出最新 `main`，再读取目录和
通知状态。GitHub 默认最多保留一个运行中和一个等待中的任务；新的触发可能
替换旧的等待任务，且不保证排队顺序，因此不要连续多次点击手动运行。
这项限制保护两个工作流之间的提交；运行期间直接向 `main` 推送其他修改仍可能
导致自动提交被拒绝，此时可在人工提交完成后重新运行。

两个工作流都会在目录相对上次成功通知发生变化时发送微信通知；增量工作流无变化时也发送
“今日无新增”，每周工作流无变化时跳过。每日工作流提交成功后还会发送已配置的邮件。

抓取和微信发送是独立步骤。微信发送失败时，工作流仍提交抓取结果与上次成功的通知
进度，最后报告失败；重跑后会继续补发尚未确认的变化，邮件步骤也可独立执行。

默认 IRSTD 查询采用宽范围候选策略：收录明确处于 infrared/infra-red 语境，并
包含 small、dim、weak、tiny、point target 或 small object 表达的论文；同时补充
IRSTD、SIRST、MIRST、MIRSTD、MFIRST 和常用数据集名称。检测、分割、跟踪、增强、
解混、数据集与评价等任务均可收录。普通可见光或声呐小目标论文不会仅因出现
“small target”而进入结果。

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
`serverchan` 初始化状态、目标指纹和已通知目录的指纹。目标指纹由仓库身份和 SendKey
生成，不保存 SendKey 原文。Fork、更换 SendKey 或从旧状态升级后，会重新发送一次
完整目录；这也适用于同一接收者轮换 SendKey。
后续只有目录实际变化时才发送，内容包括新增论文，以及标题、作者、arXiv 信息
或代码链接发生变化的论文。每日工作流没有发现变化时会发送“今日无新增”，用于
确认当天任务已正常完成；每周全量工作流没有变化时不重复发送微信通知。

后续增量通知始终只发送一条，默认最多展示 20 篇变化；可通过 `config.yaml` 的
`wechat_notification.max_papers` 修改。消息同样按 UTF-8 字节数限制在 28 KB 内，
极端情况下会安全截断。这里的代码更新指目录中的代码链接从空值变为代码仓库地址
或链接发生变化，不监控代码仓库内部的每次 commit。

没有配置 `SERVERCHAN_SENDKEY` 时，程序跳过通知并保留上次成功发送的进度；首次配置后
发送完整目录，原接收者恢复配置后补发遗漏变化。SendKey 只从环境变量或 GitHub Secret 读取，不应
写入 `config.yaml` 或提交到仓库。

可使用 `python daily_arxiv.py --notify-only` 独立重试通知，不必再次抓取。
只有全部消息成功返回后才更新通知进度；分片中途失败时，下次会重发该批消息。
如果服务已接收消息但客户端超时，或通知成功后状态提交失败，重试可能重复发送。

本地测试通知可先设置同名环境变量，再运行：

```bash
python daily_arxiv.py --notify-wechat --notify-unchanged
```

## 邮件通知配置

每日工作流在论文更新和生成文件提交成功后，会读取 `docs/wechat.md`，通过 SMTP
把排版后的日报发送到指定邮箱。HTML 正文使用论文卡片展示日期、标题、作者以及
Paper/Code 按钮，不显示 Markdown 标记、徽章源码或目录标签；不支持 HTML 的邮件
客户端会显示清理后的纯文本正文。为避免邮件客户端在 102 KB 处裁剪内容，HTML
正文使用 96 KB 安全上限；超过时只保留按日期排序后的最新论文，并在底部注明省略
数量及提供 GitHub 完整目录链接。纯文本备用正文使用相同的论文范围。邮件主题固定
为 `IRSTD-Paper-Daily`。邮件在每次增量工作流运行时发送，即使当天论文目录没有变化也会发送；
每周全量刷新工作流不会重复发信。

先在邮箱服务商后台开启 SMTP，并生成 SMTP 授权码或应用专用密码。不要使用或
提交邮箱网页登录密码。然后打开 GitHub 仓库的 **Settings → Secrets and variables
→ Actions**，添加以下 Repository secrets：

- `SMTP_HOST`：SMTP 服务器，例如 QQ 邮箱为 `smtp.qq.com`。
- `SMTP_USERNAME`：SMTP 登录账号，通常是完整发件邮箱。
- `SMTP_PASSWORD`：SMTP 授权码或应用专用密码。
- `EMAIL_TO`：收件邮箱；多个地址可以用英文逗号或分号分隔。

以下 Secrets 可选：

- `SMTP_PORT`：默认 `465`。
- `SMTP_SECURITY`：默认 `ssl`；端口 `587` 通常设置为 `starttls`。
- `EMAIL_FROM`：发件地址，默认使用 `SMTP_USERNAME`。

常见配置如下：

| 邮箱服务 | `SMTP_HOST` | `SMTP_PORT` | `SMTP_SECURITY` |
|---|---|---:|---|
| QQ 邮箱 | `smtp.qq.com` | `465` | `ssl` |
| 163 邮箱 | `smtp.163.com` | `465` | `ssl` |
| Gmail | `smtp.gmail.com` | `465` | `ssl` |
| Gmail STARTTLS | `smtp.gmail.com` | `587` | `starttls` |

全部必需 Secrets 都没有配置时，程序会安全跳过邮件。不完整的配置会让邮件步骤
失败并在 Actions 日志中列出缺少的变量，但不会打印密码。完成配置后，可手动运行
**Update IRSTD Paper Daily** 测试邮件。本地测试命令为：

```bash
python -m arxiv_daily.emailer docs/wechat.md
```

SMTP 部分拒收会报告具体失败地址；对 4xx 暂时拒收的地址重试一次，已经接受的地址
不会在该次重试中重复发送。永久拒收或重试失败会让邮件步骤失败。

## 微信版输出说明

`publish_wechat: true` 时，程序会更新：

- `docs/irstd-paper-daily-wechat.json`：微信版条目的结构化索引。
- `docs/wechat.md`：按领域分组的项目符号列表，包含论文和代码链接。

微信版文件与 Server酱通知互相独立：前者是可复制的完整 Markdown 日报，后者是
自动化工作流发送的变化摘要。

## 测试

```bash
python tests/test_smoke.py
python -m pip install pytest
python -m pytest -q
```

测试不访问网络，覆盖配置解析、增量与全量日期窗口、数据合并、Markdown 渲染、
微信渲染、Server酱通知以及代码链接校验的基本行为。回归测试还覆盖缓存丢失、配置变更、
通知失败重跑、接收目标变更、代码误匹配、限流重试、部分拒收与原子写入故障。
PR 和代码 push 会自动在 Python 3.11、3.12 上执行离线测试。

## 参考项目

本项目第一版主要参考 [Fortuneteller6/IRSTD-Arxiv-Daily](https://github.com/Fortuneteller6/IRSTD-Arxiv-Daily)，微信版输出参考 [Vincentqyw/cv-arxiv-daily](https://github.com/Vincentqyw/cv-arxiv-daily)。本仓库保留 Apache License 2.0 许可文件。
