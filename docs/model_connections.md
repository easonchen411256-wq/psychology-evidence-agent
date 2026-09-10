# 模型接入边界

Agent 默认使用本机 Codex CLI。Agent 总览的检索控制台允许在启动前选择外部的 OpenAI-compatible 模型服务；当前预置入口包括 OpenAI、DeepSeek、阿里云百炼/Qwen 和自定义服务。

## 配置字段

- `provider`：本机 Codex CLI 或外部服务类型。
- `api_base`：服务基础地址。程序会向其追加 `/chat/completions`。
- `model`：用户账号实际可用的模型 ID；不在项目内硬编码易变的模型目录。
- `api_key`：Bearer 密钥。
- `timeout_seconds`：单次结构化请求超时时间。
- `json_mode`：是否请求服务返回 JSON 对象。关闭后仍要求模型返回 JSON，项目自己的 JSON Schema/Pydantic 校验仍然生效。

OpenAI-compatible 是适配边界，不代表每个服务支持完全相同的模型能力。服务端会检查 HTTPS（仅允许回环地址使用 HTTP），模型返回值会先解析，再经过既有 schema 和领域校验；校验失败会使当前模型调用失败，不会绕过安全边界。

## 密钥生命周期

外部 API Key 只从浏览器配置表单传入当前请求，并在本地服务进程内存中供当前运行继续使用。它不会写入研究目标、计划、事件、运行快照、历史结果或任何证据产物。服务重启后，若需要继续一个外部模型运行，用户需要重新提供连接配置。

本机 Codex CLI 不需要在此页面填写云端 API Key；它继续使用既有 Codex CLI 的只读沙箱和项目策略守卫。

## 预置地址

预置地址只用于减少输入，不锁定模型 ID；服务方的模型目录和版本可能变化，使用者应以其账号可用的模型为准：

- OpenAI：`https://api.openai.com/v1`
- DeepSeek：`https://api.deepseek.com`
- 阿里云百炼/Qwen：`https://dashscope.aliyuncs.com/compatible-mode/v1`

所有外部调用都经过同一个 `StructuredOutputPort`，因此研究问题整理、规划、筛选、证据提取和草稿生成共享相同的结构化输出和安全校验边界。
