# 普度众大学生 · Psychology Evidence Agent

面向心理学论文与毕业论文场景的循证文献工作流。它把“研究问题 → 文献检索 → 标题/摘要初筛 → 合法开放获取定位 → 全文证据卡 → 多篇证据汇总 → 人工审查与综述初稿”串联为可追溯的本地流程。

[![CI](https://github.com/easonchen411256-wq/psychology-evidence-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/easonchen411256-wq/psychology-evidence-agent/actions/workflows/ci.yml) · [安全策略](SECURITY.md) · [变更记录](CHANGELOG.md) · [演示路径](docs/demo.md)

本项目由 **Eason Chen（GitHub：`easonchen411256-wq`）** 设计、开发并维护。项目所有权和版权归原作者所有；使用、修改或再发布时请保留 [AUTHORS.md](AUTHORS.md) 中的署名与许可证声明。

当前默认研究主题是：老年人跌倒恐惧相关心理干预中，心率/心率变异性（HRV）和步态指标如何用于监测过程与评估效果。

> 这是一个人机协作的研究助手，不是自动代写或自动得出研究结论的工具。标题、摘要和元数据只能用于候选筛选；正式证据判断必须回到全文与人工核查。

## 能做什么

| 模块         | 当前能力                                                                               | 结果位置                                             |
| ------------ | -------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 浏览器工作台 | Agent 总览、研究助手、双模式检索控制台、运行状态、人工审核与证据产物入口              | `data/runs/<run_id>/`；研究对话与草稿仅保存在当前浏览器会话 |
| 文献检索     | Agent 根据研究问题自动生成检索式，或接受用户手动检索式；通过 OpenAlex 获取并去重元数据 | 当前 run 的 `search_results.json`                   |
| 初步筛选     | 使用本机 Codex CLI，依据标题、摘要和元数据生成相关性、候选角色及人工审查提示           | `screened_papers.json`、`priority_reading_list.json` |
| 全文获取     | 查询 OpenAlex、Unpaywall、Europe PMC/PMC 的合法公开版本；只下载确认开放的 PDF          | `data/raw/open_access/`                              |
| 证据卡       | 从已获得授权或公开的 PDF、Markdown、TXT 中提取结构化证据卡，并做 Schema 与推断边界校验 | `data/processed/*.evidence_card.json`                |
| 证据汇总     | 生成 JSON、CSV、Markdown 比较矩阵和人工核对清单                                        | `data/processed/evidence_synthesis/`                 |
| 人工审查报告 | 生成可在 Word 中阅读和标注的文献初筛报告                                               | `literature_manual_review_report.docx`               |
| 本地网页服务 | 为浏览器工作台提供公开元数据检索与开放获取定位能力                                     | `src/psychology_evidence_agent/api_server.py`        |

## 工作流一览

```text
研究问题
  ↓
OpenAlex 元数据检索与去重
  ↓
标题 / 摘要 / 元数据初筛（Codex CLI）
  ↓
优先阅读清单 + 合法开放版本定位 + Word 人工审查报告
  ↓
用户提供或合法获得全文
  ↓
证据卡提取与本地规则校验
  ↓
多篇证据矩阵、人工核对与可追溯综述初稿
```

## 成品边界

本项目的成品目标是一个有界、可恢复的心理学证据工作流 Agent：它在检索、筛选、全文获取和证据提取阶段进行受预算约束的规划与重规划；证据综合保持确定性本地处理，草稿生成保持既有可追溯校验流程。它不会成为开放式通用 Agent。

明确不支持任意命令或 URL 执行、自动发现新工具、多 Agent 自由协作、付费墙或机构权限绕过、开放式论文结论生成、数据库/分布式调度，以及替代人工研究判断。出现全文获取、初筛不确定性或证据边界问题时，系统会暂停到 Human Gate。

## 快速开始：浏览器工作台

推荐先使用网页端查看 Agent 如何从研究问题推进到可审查的研究产物。

```powershell
cd "D:\agent试手\psychology-evidence-agent"
py -3 -m pip install -e ".[dev]"
pea web
```

程序会自动打开 `http://127.0.0.1:8000`。新的网页端采用“纵向 Agent 总览 + 运行级结果工作台”：

1. Agent 总览：先在“研究问题助手”中用自然语言描述想法，由助手澄清范围；随后在“检索控制台”选择 AI 自动检索或手动控制，并确认年份范围与候选数量后才会创建运行。
2. 当前运行：显示阶段时间线、计划、预算、事件、产物摘要和动态进度；页面不会用虚假的百分比代替实际状态。
3. 检索结果：只读取当前 run 的 `search_results.json`，展示检索条件、查询轮次和候选题录；不再维护一套独立于 Agent 的网页筛选状态。
4. 历史结果：打开本机保存的过去运行，并查看候选题录数、筛选数和待人工处理数。
5. 筛选结果：按运行逐篇查看题录、期刊、年份、作者、相关性分数、证据等级、机器判断理由和来源链接。
6. 人工审核：处理运行中的 Human Gate 决策；全文不可合法自动获取时，可以在页面选择自己合法取得的 PDF、Markdown 或 TXT 文件上传到当前 run，然后继续运行。
7. 证据产物：查看当前 run 已生成的证据相关 artifact 引用。

网页端会调用本地 Agent 运行接口，由运行工具访问 OpenAlex 元数据接口。题录结果必须继续经过筛选、全文核对和证据校验，页面不会把 OpenAlex 返回顺序标为“高相关”。人工上传的全文只保存于对应 `data/runs/<run_id>/artifacts/documents/`，服务端限制为 PDF、Markdown 或 TXT 且不超过 50 MB；研究对话、问题和检索草稿只保存在当前浏览器会话，模型 API Key 不会写入本地存储或研究产物。请仅上传你有权在本地处理的材料。原有 `/v1/literature/search` 仍保留为低层兼容/高级调试接口，但不再是普通用户的主流程。

如果服务设置了 `PEA_API_KEY`，总览页默认折叠“连接与高级设置”，并会明确说明它是本地服务访问口令，不是 OpenAI API Key。只在服务返回 401 时填写；密钥只保存在本次浏览器会话，不会写入研究 artifact。

按 `Ctrl+C` 可停止本地服务。

## 安装与统一 CLI

项目采用标准 `src` package layout。首次安装（包含测试、格式化和类型检查工具）：

```powershell
py -3 -m pip install -e ".[dev]"
```

`pea` 是统一入口；所有子命令继续使用原有参数和默认输出位置：

```text
pea doctor                 # 本机依赖、Codex CLI 与配置摘要
pea web                    # 浏览器工作台
pea search [options]       # OpenAlex 检索与可选 Codex 初筛
pea evidence FILE [options]# 从单篇全文生成证据卡
pea synthesize [options]   # 汇总证据卡
pea draft [options]        # 从汇总生成可追溯综述初稿
pea agent run "研究目标"   # 让受控 Agent 规划并执行研究流程
pea config init            # 创建可编辑的外部检索配置
pea schemas check          # 检查 Pydantic Model 与已提交 Schema 是否一致
pea schemas export         # 从 Pydantic Model 重新生成 Schema
```

兼容期内，`py -3 src/run_*.py` 仍可使用；这些文件只转发给包内实现，不再承载业务逻辑。

## 快速开始：命令行研究流程

### 1. 配置研究问题和检索式

`pea search` 默认使用安装包内置配置 `src/psychology_evidence_agent/resources/configs/default.literature_search.json`。该文件是唯一的应用默认配置来源；运行时通过 `importlib.resources` 加载，不依赖仓库根目录或当前工作目录。

要创建可编辑配置，请运行：

```powershell
pea config init
```

该命令在当前目录生成 `psychology-evidence-agent.json`，不会静默覆盖已有文件。也可从 [configs/examples/literature_search.example.json](configs/examples/literature_search.example.json) 或 [configs/examples/literature_search_expanded.example.json](configs/examples/literature_search_expanded.example.json) 开始编辑。配置优先级为：显式 `--config`、环境变量 `PEA_CONFIG`、包内默认配置。配置在检索前会校验必需字段、年份、非空查询和唯一 query ID；不完整配置会直接报错，不会运行到中途才出现字段错误。

### 2. 检索并初筛候选文献

```powershell
pea search --config psychology-evidence-agent.json --max-per-query 10 --max-screen 30
```

该命令先从 OpenAlex 获取候选论文并去重，再将最多 30 篇候选论文交由本机 Codex CLI 初筛。常见输出包括：

- `candidate_papers.json`：去重后的候选元数据
- `screened_papers.json`：标题/摘要级别的初筛结果
- `priority_reading_list.json`：建议优先人工阅读的论文
- `unscreened_papers.json`：尚未送入初筛的候选论文
- `search_report.json`：检索记录与运行信息

如只想获取元数据，不调用模型：

```powershell
pea search --skip-screen
```

### 3. 生成 Word 人工审查报告

```powershell
pea report
```

默认输出为：`data/processed/literature_search/literature_manual_review_report.docx`。报告适合逐篇标记“保留 / 排除 / 待定”，不能替代全文核对。

### 4. 定位或下载合法开放全文

```powershell
pea access
```

如需下载，显式添加 `--download`：

```powershell
pea access --download
```

程序只下载明确确认的开放 PDF，并且下载地址必须是解析到公网地址的 HTTPS 地址；不会绕过付费墙、机构登录、验证码或版权限制。没有公开版本的论文会进入 `manual_retrieval_queue.json`，需要你通过学校、图书馆或作者授权等途径获取。

### 5. 从全文生成证据卡

将已合法获得的 PDF、`.md` 或 `.txt` 放入 `data/raw/`，然后运行：

```powershell
pea evidence "data/raw/你的论文.pdf" --research-question "你的研究问题"
```

默认会在 `data/processed/` 生成同名的 `.evidence_card.json`。处理多篇资料前，建议先预览：

```powershell
pea batch --dry-run
```

确认后再执行：

```powershell
pea batch
```

### 6. 汇总证据卡并生成综述初稿

```powershell
pea synthesize
pea draft
```

前者生成证据矩阵、CSV 和人工核对队列；后者仅使用已校验证据卡中的发现与原文位置生成谨慎的 Markdown 综述初稿。它不会凭空补充文献、DOI 或机制结论。

## 本机 Codex CLI

初筛、证据卡和综述初稿功能依赖你已经登录的 Codex CLI。项目会以只读方式调用它，并继续使用本地规则校验模型输出。

在 Windows PowerShell 中，如果直接运行 `codex` 出现“禁止运行脚本”，可以使用：

```powershell
codex.cmd exec --ephemeral --skip-git-repo-check --sandbox read-only "只回复 READY"
```

看到 `READY` 代表 CLI 连接正常。此项目不需要配置 OpenAI API Key，也不读取项目中的 `.env` 作为模型密钥。

## 质量边界与研究伦理

- 初筛仅基于标题、摘要与元数据，不能替代全文阅读。
- 相关、预测、因果和干预效果必须严格区分；横断面研究不能被写成因果结论。
- HRV、心率和步态在本课题中首先是候选监测指标；直接干预证据不足时应保持“可能”“提示”“需进一步核对”等谨慎表述。
- 未被原文明确报告的信息应标为“未报告”“无法判断”或“需人工核对”。
- 只处理你有权使用的全文材料；请勿将受限全文、未发表资料或可识别研究数据上传到外部服务。

## 测试

```powershell
pytest
```

测试覆盖证据卡 Schema、推断边界、OpenAlex 去重、全文定位、初筛选择、Word 审查报告、HTTP API 与网页入口。

如果需要展示项目或进行本地验收，建议先阅读[安全演示与本地验收路径](docs/demo.md)。它区分了真实运行、离线测试和确定性 Planner 演示，避免把 Mock 结果误认为真实研究结论。

## 数据契约与 Schema

稳定的研究数据契约位于 `src/psychology_evidence_agent/domain/`，使用 Pydantic v2 Model 作为唯一来源。包内 `resources/schemas/*.json` 是由这些模型生成并提交的 Codex structured-output 工件，不应手工编辑。生成时会内联 Pydantic 的 `$ref`，以使用 Codex CLI 所需的简洁 JSON Schema 结构。

```powershell
pea schemas check   # 检查 schema drift；不一致时返回非零状态
pea schemas export  # 更新已提交的 generated schema
```

## 应用架构

业务入口通过 composition root 取得确定性的 application service；service 只依赖 Domain Model 与 Protocol port，adapter 才知道 OpenAlex、Codex CLI、开放获取 provider 和本地文档实现。详见 [docs/architecture.md](docs/architecture.md)。

Screening 与 Review Draft 通过 `Service -> StructuredOutputPort -> CodexCliAdapter` 调用结构化输出；证据综合当前是本地确定性矩阵构建，不调用模型。Unpaywall 与 Europe PMC 分别由独立 adapter 处理，并共享有限 retry 的 HTTP 基础设施。业务工件通过 `RunArtifactStore` 保存，轻量运行状态通过 `ResearchRunStore` 保存。

### ResearchRun 状态基础

项目提供轻量、可持久化的 `ResearchRun` 状态和可恢复的运行入口：

```powershell
pea run --store-root data create "在老年人跌倒恐惧相关干预中如何监测 HRV 和步态？"
pea run --store-root data start <run_id>
pea run --store-root data show <run_id>
pea run --store-root data cancel <run_id>
pea run --store-root data retry <run_id>
```

`start` 会执行已装配的搜索、初筛、合法全文定位、证据卡、汇总和初稿流程；网络与 Codex 仍由既有 adapter 提供。遇到无法自动取得的全文时，运行会停在 `waiting_for_human`。查看 `pea run show` 中的 action ID 后，可通过以下方式处理：

```powershell
pea run --store-root data resolve <run_id> <action_id> --decision provide_fulltext --fulltext "data/raw/lawful-paper.pdf"
pea run --store-root data resume <run_id>
```

也可以用 `--decision skip_paper` 跳过该论文。每个 run 的中间产物位于 `data/runs/<run_id>/artifacts/`，运行参数和阶段产物会落盘，恢复时不会把全文或大对象写入 `ResearchRun` 状态。

同一个 run 的 `start`、`resume`、`retry`、`resolve` 和 `cancel` 会使用跨平台进程锁；如果另一个进程正在处理该 run，命令会快速返回并提示稍后重试。网页端对活动 Agent 使用合作式取消：停止请求先写入持久化控制信号，Agent 在安全边界停止并进入 `cancelled`；服务重启后若发现没有后台 worker，可以从最近检查点恢复。`cancel` 后的状态是终态，不能继续 `resume`。`retry` 只接受结构化失败中标记为可重试的失败；它会复用输入指纹仍然匹配的已完成工作单元。

状态保存于 `data/runs/<run_id>/state.json`。`ResearchRun` 只保存任务身份、`RunStatus`、`RunStage`、artifact 引用、搜索轮次摘要、失败元数据和轻量 Human Gate action；实际研究产物仍由 `ArtifactStore` 管理。可恢复工作单元的状态保存于每个 run 的 `artifacts/workflow_checkpoints.json`，大对象仍保存在独立 artifact 中。`RunStateMachine` 负责确定性合法转换，`StepExecutor` 一次只执行当前阶段的注入 Service handler，`EvidenceAgent` 负责协调启动、连续执行、暂停、恢复后的继续执行、失败和完成。详见 [docs/agent_runtime.md](docs/agent_runtime.md)。

## 受控研究 Agent

在需要由系统根据目标自主决定下一步时，使用 `pea agent`。它会基于当前运行状态生成结构化计划，再由本地策略守卫检查工具白名单、阶段依赖、Human Gate 和预算后执行：

```powershell
pea agent run "系统梳理老年人跌倒恐惧干预中 HRV 和步态指标的监测证据" --max-steps 40
pea agent run "系统梳理老年人跌倒恐惧干预中 HRV 和步态指标的监测证据" `
  --research-question "正念干预如何影响老年人的跌倒恐惧和 HRV？" `
  --population "老年人" `
  --intervention-or-exposure "正念心理干预" `
  --outcome "跌倒恐惧" --outcome "HRV" `
  --include "同行评议研究" --year-from 2015 --year-to 2026
pea agent status <run_id>
pea agent plan <run_id>
pea agent events <run_id>
pea agent resume <run_id>
pea agent retry <run_id>
```

`objective` 是 Agent 要完成的总体任务；`--research-question` 和其余研究范围参数
会形成可校验的 `ResearchBrief`，并保存为当前 run 的 `agent/research_brief.json`。
不提供结构化参数时，系统会继续使用 objective 作为 research question，以兼容已有用法。

网页端对应的 Agent 状态接口为 `GET /v1/agent/runs/{run_id}`，运行创建、恢复、重试、取消和 Human Gate 决策接口均使用同一服务端 run 根目录。状态快照中的 `cancel_requested` 表示停止请求已登记，`recovery_available` 表示服务重启或 worker 中断后可从检查点恢复。后台运行失败会转换为结构化的 `ResearchRun` 失败状态，不会把异常正文写入事件摘要。

默认 Planner 使用本机 Codex CLI；如果只想离线验证规划层，可显式使用确定性 Planner。实际 workflow 工具仍会按阶段调用 OpenAlex、Codex 或开放获取 provider：

```powershell
pea agent run "离线测试研究问题" --deterministic-planner
```

项目默认使用兼容性更好的 `gpt-5.5` 作为 Codex CLI 模型，并通过 `--model` 显式传入，避免继承本机配置中与 CLI 版本不匹配的模型。已升级到较新 Codex CLI 的环境可以设置 `PEA_CODEX_MODEL` 覆盖：

```powershell
$env:PEA_CODEX_MODEL = "gpt-5.6-sol"
pea agent run "测试研究问题"
```

Planner 只能提出已注册的 `workflow.*`、`search.*`、`screen.*`、`fulltext.*` 或 `evidence.*` 工具调用，不能直接访问网络、执行命令、修改 Prompt/Schema 或绕过现有证据校验。全文阶段会从已验证的筛选 artifact 创建受限队列，逐批复用合法开放获取服务；证据阶段会从全文 artifact 创建受限提取队列，并继续使用既有 Evidence Schema 与 inference-boundary validation。没有合法全文时暂停到既有 Human Gate，不允许绕过付费墙。每次工具执行都会记录计划、结果和事件；研究目标、计划、预算与事件保存在对应 run 目录中，论文全文和模型输出正文不会写入 Agent event trace。Agent 可以在搜索结果不足、provider 失败或人工决定后重新规划，但受最大步骤、重规划、模型调用和单步尝试次数限制。

## 项目目录

```text
psychology-evidence-agent/
├─ src/
│  ├─ psychology_evidence_agent/
│  │  ├─ domain/     Pydantic v2 核心数据契约
│  │  │  └─ run.py    ResearchRun 状态契约
│  │  ├─ adapters/persistence/  ArtifactStore 与 ResearchRunStore 文件实现
│  │  ├─ runtime/      状态机、Human Gate 与生命周期编排
│  │  │  ├─ agent_controller.py  受控计划/执行/重规划循环
│  │  │  ├─ policy_guard.py      工具与预算策略校验
│  │  │  └─ plan_executor.py     单步 Agent 工具执行
│  │  ├─ services/agent_tools/  受控 Agent 工具 package
│  │  │  ├─ search_screening.py 搜索、筛选与固定阶段注册实现
│  │  │  ├─ fulltext.py        队列化合法全文工具实现
│  │  │  └─ evidence.py        队列化证据卡工具实现
│  │  ├─ services/fulltext_tools.py  旧导入路径的兼容转发
│  │  ├─ services/evidence_tools.py  旧导入路径的兼容转发
│  │  ├─ services/planning.py     确定性/结构化 Planner
│  │  ├─ domain/agent.py           Agent 计划、预算与事件契约
│  │  ├─ adapters/persistence/event_store.py  append-only 事件存储
│  │  ├─ services/workflow.py  真实业务 Service handler 的装配桥接
│  │  ├─ resources/  内置 Prompt、Schema、默认配置与浏览器静态资源
│  │  └─ ...         可导入的业务 package、HTTP API 与 CLI 实现
│  └─ run_*.py                    兼容 wrapper（仅转发，不含业务逻辑）
├─ configs/examples/  可编辑的用户配置示例（通过 --config 显式使用）
├─ data/raw/     已合法获得的原始论文材料
├─ data/processed/  候选文献、证据卡、矩阵与报告输出
├─ docs/         产品、流程、规则和接口文档
├─ evals/        评估计划和评分规则
└─ tests/        自动化测试
```

## 推荐阅读顺序

1. [docs/01_product_spec.md](docs/01_product_spec.md)
2. [docs/02_workflow.md](docs/02_workflow.md)
3. [docs/03_evidence_rules.md](docs/03_evidence_rules.md)
4. [docs/07_literature_retrieval.md](docs/07_literature_retrieval.md)
5. [docs/10_fulltext_and_review_draft.md](docs/10_fulltext_and_review_draft.md)

## 仓库协作与安全

- [AUTHORS.md](AUTHORS.md)：原始作者、项目所有权和贡献说明。
- [CITATION.cff](CITATION.cff)：用于 GitHub 和学术场景的标准引用信息。
- [SECURITY.md](SECURITY.md)：凭据、论文内容、外部服务和漏洞报告边界。
- [.env.example](.env.example)：环境变量参考模板；项目不会自动加载 `.env` 文件。
- [CHANGELOG.md](CHANGELOG.md)：版本和工程里程碑记录。
- [docs/demo.md](docs/demo.md)：本地演示、离线验收和工程设计重点。

当前仓库公开可见，但代码仍采用 `LicenseRef-Proprietary`；公开可见不等于自动获得复制、修改或商业使用许可。第三方依赖不采用本项目的许可证，各自条款以其包元数据和文档为准。

## 作者与引用

如果你在研究记录、技术文章或二次开发中使用本项目，请保留作者署名并引用 [CITATION.cff](CITATION.cff)。开源许可证只授予许可证明确允许的使用权，不会转移原作者的版权和项目所有权。
