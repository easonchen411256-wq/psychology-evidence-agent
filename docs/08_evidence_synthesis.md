# 多篇证据卡汇总

## 目标

将已通过本地校验的多篇证据卡并列为可人工核对的证据矩阵。该步骤只整理已有卡片中的内容，不重新阅读原文、不生成新的研究结论，也不调用 Codex CLI。

## 输入

- 默认读取 `data/processed/` 下以 `.evidence_card.json` 结尾、且文件名不以 `sample_` 开头的文件，避免练习样例混入真实综述。
- 也可用 `--input` 明确指定一张或多张证据卡。
- 输入卡片若不是 JSON 对象，或缺少证据卡的必要字段，将被记录为跳过项，而不是被补写。

## 输出

默认写入 `data/processed/evidence_synthesis/`：

- `evidence_synthesis.json`：机器可读的汇总结果；
- `evidence_matrix.csv`：可用 Excel 打开的横向比较表；
- `evidence_matrix.md`：适合在 VS Code 中阅读的表格；
- `review_queue.md`：需要回到原文确认的项目。

## 证据角色

汇总器不再根据关键词自动将论文标为直接、相邻或背景证据。因为“未测量 HRV”“不属于心理干预”等否定表述同样会出现关键词，自动分类可能误导。

每张卡默认标为 `review_required`。`signals` 只表示术语曾在卡片中出现，不表示该指标被实际测量、报告或改善。最终证据角色必须由人工阅读全文后确认。

## 运行

在项目根目录运行：

```powershell
py -3 src/run_evidence_synthesis.py
```

如只汇总指定的证据卡：

```powershell
py -3 src/run_evidence_synthesis.py --input data/processed/paper_01.evidence_card.json data/processed/paper_02.evidence_card.json
```

若目标输出已存在，确认需要替换时再加 `--overwrite`。

如需连同练习样例一起汇总，可加 `--include-sample`。

## 人工核对顺序

1. 先读 `review_queue.md`，补充材料不完整或存在待核对项的论文。
2. 打开 `evidence_matrix.md`，比较干预、样本、HRV/心率、步态及主要发现。
3. 将候选角色改为你确认后的直接、相邻或背景证据，再用于论文写作。
