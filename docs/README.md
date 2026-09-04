# IRSTD Paper Daily 使用说明

本项目每天从 arXiv 搜索红外小目标检测（IRSTD）相关论文，维护历史 JSON 数据，并生成三种输出：

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

本地首次运行会访问 arXiv API；启用代码匹配后还会访问 GitHub Search API。GitHub Search API 未认证时限流较低，建议在 Actions 中运行完整更新。

要为历史论文补齐代码链接：

```bash
python daily_arxiv.py --backfill_code
```

已有代码链接的论文会跳过，不会重复查询。

## 配置新领域

在 `config.yaml` 的 `domains` 下增加条目即可：

```yaml
domains:
    "New Domain":
        enable: true
        max_results: 10
        filters:
            - "keyword1"
            - "keyword phrase 2"
```

`enable: false` 会停止抓取该领域的新论文，但不会删除历史数据。含空格的过滤词会按完整短语搜索，多个过滤词以 `OR` 连接。

## 微信版输出说明

`publish_wechat: true` 时，程序会更新：

- `docs/irstd-paper-daily-wechat.json`：微信版条目的结构化索引。
- `docs/wechat.md`：按领域分组的项目符号列表，包含论文和代码链接。

该功能生成可复制的微信版日报，不直接调用微信 API，也不会发送消息。若需要自动发送，应在仓库外另行配置合规的企业微信/微信公众号服务。

## 测试

```bash
python tests/test_smoke.py
```

测试不访问网络，覆盖配置解析、数据合并、Markdown 渲染、微信渲染以及代码链接校验的基本行为。

## 参考项目

本项目第一版主要参考 [Fortuneteller6/IRSTD-Arxiv-Daily](https://github.com/Fortuneteller6/IRSTD-Arxiv-Daily)，微信版输出参考 [Vincentqyw/cv-arxiv-daily](https://github.com/Vincentqyw/cv-arxiv-daily)。本仓库保留 Apache License 2.0 许可文件。
