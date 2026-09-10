# 文献人工审查报告

检索 JSON 适合程序处理，但不适合逐篇阅读。本功能将已有的候选论文、初筛结果、优先阅读清单和全文获取结果整理成 Word 报告，供人工审查；它不会再次调用 Codex CLI，也不会改变初筛评分。

## 报告内容

- 首页概览：研究问题、候选数、已初筛数、优先阅读数，以及已获得/待获取全文数；
- 审查索引：每篇文献的题名、年份、初筛得分、证据层级、全文状态与空白人工决定栏；
- 逐篇阅读卡：完整题名、作者、期刊、DOI、摘要、初筛理由、人工核对提示、全文来源与“保留 / 排除 / 待定”记录区。

摘要、初筛理由和全文状态只用于帮助你决定是否阅读全文；它们不是全文级研究结论。尤其对于本项目，步态和 HRV 的相邻证据不能自动当成跌倒恐惧心理干预的直接疗效证据。

## 使用方式

先安装一次 Word 生成依赖：

```powershell
py -3 -m pip install -r requirements.txt
```

从扩展检索结果生成优先阅读报告：

```powershell
py -3 src/run_literature_review_report.py --search-dir data/processed/literature_search_expanded
```

默认输出为：

```text
data/processed/literature_search_expanded/literature_manual_review_report.docx
```

若需将全部已初筛论文也逐篇写入报告：

```powershell
py -3 src/run_literature_review_report.py --search-dir data/processed/literature_search_expanded --all-screened --output data/processed/literature_search_expanded/literature_manual_review_report_all.docx
```

`--abstract-limit 0` 可保留完整摘要；默认每篇最多显示 1200 个字符，避免报告因少量超长摘要变得难以阅读。输出文件已存在时，程序会停止，确认替换时再加 `--overwrite`。
