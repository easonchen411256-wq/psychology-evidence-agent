# 全文获取与综述初稿

## PDF 直接读取

`run_agent.py` 与 `run_batch_agent.py` 可直接接收本地 PDF。程序仅在本机用 `pypdf` 提取可检索文字，再交由既有证据卡流程分析。

- 可检索 PDF：自动提取文字、在每页前加入页码标记并生成证据卡；证据卡中的原文位置应结合这些页码回查；
- 扫描版 PDF：若没有可提取文字，明确提示需要 OCR 或人工提供文本；
- 加密 PDF：明确提示需要使用可读取版本；
- 图、表和复杂版式：文本提取不保证正确还原，相关结论仍应通过原文人工核对。

示例：

```powershell
py -3 src/run_agent.py "data/raw/paper_01.pdf" --research-question "你的研究问题"
```

## 多来源合法全文获取

默认从初筛后的 `priority_reading_list.json` 取论文，再从完整候选集补齐题名和 OpenAlex ID。每篇论文会依次检查：

1. OpenAlex 明确标记的开放 PDF；
2. Unpaywall 返回的合法开放版本（仅当你提供查询所需的邮箱参数时启用）；
3. Europe PMC / PMC 记录中的开放获取 PDF。

程序会把每个来源、许可信息、链接和选择结果保存在 `open_access_candidates.json`。同一篇论文有多个版本时，会优先选择明确的 PDF 链接；不确定许可、只有落地页或无法确认 PDF 的链接不会自动下载。

对明确标记为开放获取、且已验证为 PDF 的条目，可由用户显式启用下载。下载地址必须使用 HTTPS 并解析到公网地址，程序拒绝本地网络地址和重定向；下载后的文件会放在 `data/raw/open_access/`，再由 PDF 或批量证据卡功能处理。

未自动获得全文的论文会自动写入 `manual_retrieval_queue.json`。其中会列出题名、DOI、文章页、已检查来源、失败或缺失原因，以及下一步建议；你只需要人工处理这份小队列，而不是重新逐篇搜索全部候选文献。

先生成开放获取清单：

```powershell
$env:UNPAYWALL_EMAIL = "你的学校邮箱@example.edu"
py -3 src/run_open_access_lookup.py
```

确认清单后，才下载其中明确的开放 PDF：

```powershell
py -3 src/run_open_access_lookup.py --download
```

不提供 `--unpaywall-email` 时，程序仍会使用 OpenAlex 和 Europe PMC / PMC；只是不会查询 Unpaywall。邮箱只会发送给 Unpaywall 作为其公开 API 的请求参数，不写入项目文件或输出报告。也可以先在当前 PowerShell 会话中设置 `$env:UNPAYWALL_EMAIL`，再省略该命令参数。

每次启用下载都会生成 `open_access_download_report.json`，逐篇记录下载成功、HTTP 403、非 PDF 响应、文件过大或其他失败原因；不会把“有开放链接”误报为“已下载”。程序不会绕过付费墙、校园网/机构登录、验证码或版权限制；这类论文会进入人工队列，并附 DOI 或文章页链接供你通过学校图书馆等已授权途径获取。

如需对全部候选论文（而不仅是优先阅读清单）查找开放全文，再加 `--all-candidates`。

## 综述初稿

综述初稿只使用已生成证据卡中的 `supported_claims`、`findings`、研究设计、局限和人工核对项。每条事实性主张必须绑定到对应证据卡的具体 `finding` 与 `evidence_location`；若无法绑定，初稿不写入。它不自动创建参考文献或编造引文。

初稿固定包含：研究问题、证据范围、三个主题小节（跌倒恐惧与干预、步态监测、心率/HRV 监测）、证据边界与下一步。材料不足时，初稿会明确说明直接证据有限。

```powershell
py -3 src/run_review_draft.py
```
