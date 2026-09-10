# 输出结构

证据卡应包含：

1. 文献信息
2. 材料完整度
3. 研究问题与假设
4. 研究设计、样本与测量
5. 主要结果
6. 局限性
7. 可支持的写作表述
8. 不可直接支持的写作表述
9. 人工核对项

机器可读字段由 `src/psychology_evidence_agent/domain/evidence.py` 的 Pydantic Model 定义；`resources/schemas/evidence_card.schema.json` 是其生成工件，供 Codex CLI 使用。请运行 `pea schemas export` 重新生成，或运行 `pea schemas check` 检查 drift；不要手工编辑该 JSON 文件。

## 写入前的两层检查

1. Codex JSON Schema：限制模型最终输出结构。
2. Pydantic：对解析后的 JSON 执行同一份结构契约校验。
3. 本地证据规则：检查研究设计与推论强度是否冲突，以及不完整材料是否留下人工核对项。

可单独检查一张卡：

```powershell
py -3 src/run_check.py data/processed/你的文件.evidence_card.json
```
