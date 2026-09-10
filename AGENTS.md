# 项目协作规范

## 项目目标

本项目只处理心理学研究证据的提取、核对和结构化整理；不替代人工研究判断。

## 基本原则

- 不编造论文内容、引用、样本量或统计结果。
- 区分相关、预测、因果与干预效果。
- 证据不足时明确标记“未报告”“无法判断”或“需人工核对”。
- 未经明确授权，不将未发表材料或可识别数据发送到外部服务。
- 修改提示词、规则或 schema 后，应同步更新评估样例。结构化输出的 schema 必须从 `domain/` Pydantic v2 模型生成：运行 `pea schemas export`，再运行 `pea schemas check`；不得手工编辑 generated schema。
- 通过 Codex CLI 分析论文时，保持 `read-only` 沙箱，不允许模型修改项目文件。

## 变更顺序

先更新 `docs/` 中的规则，再更新 `prompts/`、`schemas/` 和测试材料。

## 项目结构

- 业务 Python 代码位于 `src/psychology_evidence_agent/`，使用包内相对导入；不得通过 `sys.path` 注入导入项目模块。
- `src/run_*.py` 仅是兼容 wrapper；新命令应通过已安装的 `pea` CLI 调用。
- 内置运行时 prompts、schemas、web 静态资源和默认配置位于 `src/psychology_evidence_agent/resources/`，并通过 `importlib.resources` 读取；`resources/configs/default.*` 是唯一 canonical default，不得以仓库根目录路径读取或复制维护。`domain/` 中的 Pydantic Model 是核心数据结构契约；`resources/schemas/` 是 committed generated artifact。
- 根目录 `configs/examples/` 仅保存可编辑的示例、Demo 或用户参考配置；通过 CLI 的 `--config` 显式使用。检索配置优先级为 `--config`、`PEA_CONFIG`、包内 default。`data/` 与 `logs/` 是当前工作目录下的本地工作产物。
- `python-docx` 只能位于 `documents/word_exporter.py` 这类文档导出 adapter；上层必须通过 `ReportExporter` 协议调用，不能操作 `Document`。当前受支持版本自带类型标记；若未来依赖版本确实缺失可靠类型信息，仅可增加 `docx`/`docx.*` 的局部 mypy compatibility override，禁止全局忽略。
- `services/` 的业务 service 只能依赖 `domain/` 和 `ports/`，不得导入 `httpx`、`subprocess`、`pypdf` 或 concrete adapter。`services/workflow.py` 只负责把既有 service 组合成注入给 runtime 的阶段 handler，仍不得接触具体 provider/文档实现。外部 URL、原始 provider JSON、subprocess 与 HTTP 错误映射仅可位于 `adapters/`；`bootstrap.py` 是唯一绑定 concrete adapter 与 service 的 composition root。
- Screening 使用 `ScreeningService` 依赖 `StructuredOutputPort`；当前 evidence synthesis 是确定性的本地处理，不应为架构形式强行增加模型调用。业务产物使用 `RunArtifactStore`，轻量运行状态使用 `ResearchRunStore`，具体文件系统路径只能由 composition root 注入。
- `ResearchRun` 只保存可序列化的任务状态、artifact 引用、轮次摘要和结构化失败信息；不得嵌入论文、EvidenceCard、Synthesis 或 Draft 大对象。`runtime/` 提供基础状态转换、Human Gate 和最小生命周期编排，不包含具体业务算法。
- `adapters/persistence/run_lock.py` 提供跨 Windows/Linux 的单 run 进程锁。`start`、`resume`、`resolve` 和 `cancel` 等会推进既有 run 的 CLI 生命周期命令必须在锁覆盖的 load -> execute/copy -> persist 临界区内运行；`create` 由 `ResearchRunStore` 的状态锁保护。锁文件由操作系统在进程退出时释放，不得通过删除锁文件“解锁”。`ResearchRun.revision` 还会拒绝陈旧快照覆盖较新的状态。
- `adapters/persistence/cancellation_store.py` 保存网页后台 Agent 的合作式取消信号。活动 worker 持有 run lock 时，API 只能写入该控制信号；`AgentController` 在安全边界读取后通过状态机进入 `CANCELLED`。取消信号不属于 `ResearchRun` 领域字段，服务重启后的 `RUNNING` 孤立 run 通过既有 `resume` 从 checkpoint 恢复。
- `runtime/state_machine.py` 是唯一的 deterministic 状态转换规则来源；它不得依赖 service、adapter、网络或文件系统。`runtime/step_executor.py` 一次只执行当前阶段的注入 handler，不修改下一阶段，也不得依赖 concrete adapter。`runtime/orchestrator.py` 只负责 start、执行、保存、暂停、失败和完成协调；真实阶段 handler 由 `services/workflow.py` 提供并由 `bootstrap.py` 注入。
- `services/checkpoints.py` 负责细粒度工作单元的恢复元数据。checkpoint 只能保存状态、输入指纹、尝试/错误元数据和 artifact key，不得嵌入全文、论文列表、EvidenceCard、Prompt 或模型输出正文。只有输入指纹匹配的已完成单元可以复用；Prompt、Schema、研究问题或阶段输入变化必须使旧单元失效。`pea run retry` 只能恢复结构化失败中明确标记为 retryable 的 run，必须继续遵守 run lock、revision 和 Human Gate。
- `runtime/agent_controller.py` 是受控 Agent 的唯一执行循环。Planner 输出只能作为提案，必须先通过 `PlanPolicyGuard`，再由 `PlanExecutor` 调用已注册的 `workflow.*`、`search.*`、`screen.*`、`fulltext.*` 或 `evidence.*` 工具；禁止开放式工具名、任意代码/命令、任意 URL 或跳过当前 RunStage。所有 Agent 运行必须受 `ExecutionBudget` 限制，并在达到预算、策略拒绝或不可重试失败时停止。
- `evidence.prepare` 与 `evidence.process_next` 的队列只保存 artifact key、paper ID、输入指纹和状态，不保存论文正文；正式 `EVIDENCE_CARD` 引用只能由真实生成或重新验证的证据卡产生。证据提取必须继续通过 `EvidenceExtractionService`、JSON Schema 和 `validate_evidence_card`，不能让 Planner 直接读取或解释论文材料。
- Agent planner 的输入只能包含研究目标、轻量状态摘要、artifact 类型、已完成步骤和工具描述，不得把论文全文、原始 provider payload 或模型内部推理写入计划/事件。`events.jsonl` 为 append-only 运行轨迹；事件只记录摘要、ID、指纹、artifact reference 和受控元数据。
- `runtime/human_gate.py` 通过 `RunStateMachine` 和 `ResearchRunStore` 实现 pause/resolve/resume-to-running。暂停只改变 RunStatus，不改变 RunStage；PendingHumanAction 与 HumanDecision 必须保持轻量、可序列化，且不得覆盖原 AI artifact 或保存 Prompt、全文、模型推理过程。
- CI 中必须 mock OpenAlex、Unpaywall、Europe PMC/PMC 与 Codex CLI；测试不得真实访问网络、模型或论文全文。
- 不提交 `data/raw/`、`data/processed/`、`logs/` 中的材料或生成结果。
