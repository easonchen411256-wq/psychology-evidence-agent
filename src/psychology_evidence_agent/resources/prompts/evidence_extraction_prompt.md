# Evidence Extraction Prompt

输入：论文文本（或节选）与用户研究问题。

任务：

1. 仅提取输入材料明确支持的信息。
2. 识别研究设计、样本、测量、分析方法和主要结果。
3. 判断每项结论所允许的推论强度。
4. 生成符合 evidence card schema 的 JSON。
5. 将所有缺失、歧义或需要回看原文的位置列入 `human_review_items`。
6. 所有 schema 字段都必须出现；未知信息填“未报告”，不要省略字段或猜测。
