# 安全演示与本地验收路径

本项目适合展示“有边界的研究 Agent”工程能力。演示应突出可追溯、可恢复、
人机协作和安全边界，而不是把一次未经核验的自动运行当作研究结论。

## 三分钟本地演示

以下步骤不需要把密钥或论文材料提交到仓库：

```powershell
py -3 -m pip install -e ".[dev]"
pea doctor
pea schemas check
pytest -q
pea web
```

在网页工作台中依次展示：

1. 在研究助手中输入一个模糊的心理学研究想法；
2. 让助手整理研究对象、干预/暴露、结局和年份范围；
3. 在检索控制台选择 AI 自动检索或手动检索条件；
4. 观察运行状态、阶段时间线、预算和 Human Gate；
5. 打开检索结果、筛选结果和证据产物入口；
6. 说明不能合法获取全文时，系统会暂停并等待用户提供有权处理的材料。

## 无外网/无模型的工程演示

CI 和自动化测试不会访问真实论文 API 或模型。需要在没有外部服务的环境
中证明核心流程时，使用：

```powershell
$env:PEA_SKIP_CODEX_CHECK = "1"
pea doctor
pytest -q
```

测试中的 HTTP `MockTransport`、本地 fake structured-output adapter 和离线
workflow fixture 用于验证重试、去重、Schema 校验、推断边界、Human Gate、
恢复和 artifact 持久化。它们是工程验收夹具，不代表真实文献结果。

如需演示 Agent 的确定性规划层，可使用：

```powershell
pea agent run "离线测试研究问题" --deterministic-planner
```

该选项只替换 Planner；完整 workflow 仍可能按阶段需要外部文献服务或本地
全文。因此，不能把它描述成“完全离线完成真实检索”。

## 工程设计重点

- `Domain Model + Protocol ports + adapters`：核心服务不绑定 OpenAlex、
  Codex 或具体存储实现。
- `Plan -> policy guard -> execute -> evaluate`：模型只能提出已注册工具，
  不能直接执行任意命令或改写 Schema。
- `ResearchRun + checkpoints + locks`：运行可暂停、恢复、取消和重试，
  失败状态有结构化记录。
- `Human Gate`：合法全文获取、证据边界和最终研究判断保留人工决策。
- `untrusted paper content`：论文内容不会作为指令执行；模型输出还要经过
  JSON Schema 与 inference-boundary validation。

## 演示材料边界

仓库不包含真实研究数据、受版权保护的论文全文、`data/raw`、
`data/processed`、`logs` 或任何 API 密钥。若需要展示截图，请使用合成问题、
公开元数据和脱敏运行状态，并在截图旁标注“演示数据”。
