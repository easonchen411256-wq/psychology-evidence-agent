# 文献检索与初筛

## 范围

本功能用于检索公开文献元数据、去重，并基于标题和摘要做初筛。它不替代数据库的系统检索，也不下载全文，更不把检索结果当作已被证实的结论。

## 主题与查询

默认主题、研究问题和六组英文查询位于包内 `src/psychology_evidence_agent/resources/configs/default.literature_search.json`。查询分为：心理干预与跌倒恐惧、步态、HRV、自主神经、正念、CBT 与综合监测。

不要要求每篇论文同时包含全部关键词。直接证据可能稀少，步态和 HRV 的相邻或背景证据应单独标记，不能被误写为直接疗效证据。

当初筛只得到跌倒恐惧的测量或风险因素研究时，可显式使用 `configs/examples/literature_search_expanded.example.json`。该配置额外覆盖 CBT、正念、焦虑调节、双任务步态、步态变异性、跌倒预防项目与自主神经/HRV；它保留三条平行证据路线，而不强迫每篇论文同时出现所有概念。

## 工作流

1. 从 OpenAlex 获取公开元数据与摘要（如可用）。
2. 以 DOI、OpenAlex ID 或标准化标题去重。
3. Codex CLI 根据标题和摘要对候选论文评分并标记证据层级。
4. 保存候选集、初筛结果、优先阅读清单和检索元数据。
5. 使用多来源全文获取功能，自动查找并下载合法开放版本，同时生成未获取论文的人工队列。
6. 只对人工队列中仍未获取的高优先级论文，使用学校图书馆或其他已授权途径补齐全文，再使用证据卡 Agent 做全文级提取。
7. 生成 Word 版人工审查报告，先阅读优先论文的摘要、初筛理由和全文状态，再人工决定保留、排除或待定。

初筛时，候选论文会先按命中查询组数排序：多条独立查询都命中的论文优先。每次运行都会保存 `unscreened_papers.json`，并在 `search_report.json` 中写入已筛数量、未筛数量与选择策略。只有使用 `--screen-all` 完成全部候选的标题/摘要筛选后，才能对“本次候选中是否存在直接证据”作范围性判断。

## 运行

```powershell
py -3 src/run_literature_search.py
```

如需筛选本次检索的全部候选论文：

```powershell
py -3 src/run_literature_search.py --screen-all
```

扩展检索示例（写入独立目录，以保留原始结果）：

```powershell
pea search --config configs/examples/literature_search_expanded.example.json --max-per-query 20 --max-screen 60 --output-dir data/processed/literature_search_expanded
```

默认会检索每组查询的 15 条记录，并最多使用 Codex CLI 初筛 30 篇去重后的候选。先用 `--skip-screen` 只检查检索结果，或用 `--max-per-query 5 --max-screen 10` 进行低成本试运行。
