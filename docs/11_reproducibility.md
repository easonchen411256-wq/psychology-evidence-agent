# 可复现性与版本记录

本项目把论文原文、模型输出和运行环境分开管理：原始论文与生成结果可能受版权、隐私或反复运行影响，默认不提交；代码、规则、提示词、schema、检索配置和测试则应被版本控制。

## 首次启用 Git

项目根目录已初始化为 Git 仓库。请在确认本次改动后运行：

```powershell
git status
git add README.md AGENTS.md .gitignore requirements.txt configs docs prompts schemas src tests evals examples
git commit -m "Initialize reproducible psychology evidence agent"
```

`data/raw/`、`data/processed/` 与 `logs/` 默认被 `.gitignore` 排除；不要把受版权保护的全文、未发表材料、个人信息或 Codex 的登录资料提交到仓库。

## 每次正式运行前

```powershell
py -3 -m pip install -r requirements.txt
py -3 src/environment_check.py
py -3 -m unittest discover -s tests
```

环境检查会报告 Python、依赖版本、Codex CLI 版本以及当前配置文件的 SHA-256 摘要。它只读取本机信息，不发送论文内容，也不修改文件。

## 记录一次可追溯运行

对要用于论文的检索、初筛或证据卡运行，建议把完整 PowerShell 命令、运行日期、输入文件来源与输出路径写进一个不含敏感信息的日志文件；例如：

```powershell
New-Item -ItemType Directory -Force logs | Out-Null
@'
日期：2026-08-07
问题：在老年人跌倒恐惧相关的心理干预中，心率及 HRV 和步态指标如何被用于监测干预过程与评估效果？
命令：pea search --config configs/examples/literature_search_expanded.example.json --max-per-query 20 --screen-all --output-dir data/processed/literature_search_expanded_all
输入来源：OpenAlex 公开元数据；后续全文仅使用有授权或开放获取版本。
输出：data/processed/literature_search_expanded_all/
'@ | Set-Content -Encoding UTF8 logs/2026-08-07_search.md
```

日志默认不提交，避免意外记录本地路径、论文内容或个人信息。若要共享运行记录，请先人工去敏，再将副本保存到 `examples/` 或 `docs/`。

## 复现实验的最小信息

至少保留：Git 提交号、研究问题、配置文件名与摘要、检索日期、完整命令、候选数量、筛选数量、下载报告、原文文件名与人工核对结论。模型或 OpenAlex 返回结果会随时间变化，复现时应报告差异，而不是假定结果完全相同。
