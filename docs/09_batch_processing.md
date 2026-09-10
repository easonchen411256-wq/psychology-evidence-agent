# 批量生成证据卡

## 目标

当用户已经取得多篇公开论文的可检索文本后，将 `data/raw/` 及其子目录内的 `.md`、`.txt` 或可检索 PDF 文件逐篇送入既有证据卡流程。每篇仍独立生成、独立校验；批量处理不会把多篇论文混成一张卡。

## 安全与成本边界

- 每篇论文均通过 Codex CLI 的 `read-only` 沙箱分析；
- 默认最多处理 5 篇，避免意外消耗大量 Codex 使用量；
- 已有同名证据卡时默认跳过，不覆盖已有结果；
- 输出会保留 `data/raw/` 下的相对目录。例如 `data/raw/open_access/paper.pdf` 会生成 `data/processed/open_access/paper.evidence_card.json`，避免不同子文件夹中的同名论文互相覆盖；
- 单篇失败会记录在终端中，后续论文仍继续处理；
- 仅处理用户放入 `data/raw/` 的 `.md`、`.txt` 或 PDF 文件；不上传未授权的未发表材料。

## 运行

在项目根目录运行：

```powershell
py -3 src/run_batch_agent.py --research-question "在老年人跌倒恐惧相关的心理干预中，心率及心率变异性（HRV）和步态指标如何被用于监测干预过程与评估效果？这些指标分别可能反映哪些情绪、认知和运动变化？"
```

先只检查会处理哪些文件、不调用 Codex：

```powershell
py -3 src/run_batch_agent.py --dry-run
```

调整本次上限，例如处理 3 篇：

```powershell
py -3 src/run_batch_agent.py --max-files 3
```

若确认要重新生成同名证据卡，再加 `--overwrite`。批量完成后，运行 `src/run_evidence_synthesis.py --overwrite` 更新多篇证据矩阵。
