# 可移除的 Claude CLI 桥接模块

此目录独立提供本机 OpenAI Chat Completions 接口，内部调用已登录的 `claude -p`。
主项目没有导入它，没有新增依赖，也没有修改默认模型配置、检查点格式或当前实验。
开源时可直接删除整个 `integrations/claude_cli/` 目录。

## 启动和接入

在同一用户下先完成 Claude Code 的订阅登录。模块不提取、保存或转发登录令牌。
本机核对的 CLI 版本为 2.1.309；2026-10-23 已用 Max 订阅和 `claude-haiku-5-5`
完成原生 ReAct 的真实模型、六种公共工具及评估反馈联调，见
[验收记录](../../docs/records/experiments.md#2026-10-23-claude-haiku-55-react-订阅联调)。
自动化测试仍全部使用本目录的合成 CLI；真实调用作为独立手动验收。
认证仍由 CLI 管理，账号权限和订阅额度仍由服务端控制。
参见 [Claude Code 认证](https://code.claude.com/docs/en/authentication)。

从项目根目录启动独立进程：

```bash
python3 integrations/claude_cli/server.py --port 8765 --timeout 1800
```

只监听 `127.0.0.1`。`--command` 可指定 Claude 可执行文件的绝对路径；不通过 shell 执行。
`GET http://127.0.0.1:8765/health` 只检查桥接进程，不发起模型调用。

需要启用时，在**新实验使用的** `configs/<method>.toml` 中设置下面的 Chat 字段：

```toml
chat_model = "sonnet"
chat_base_url = "http://127.0.0.1:8765/v1"
chat_key_env = "ALPHA_ATLAS_LOCAL_BRIDGE_UNUSED"
timeout_seconds = 1800.0
```

可换成账号可用的完整 Claude 模型 ID。正式比较应固定完整 ID，并记录 CLI 版本；别把
`deepseek-flash` 等原服务的模型名直接发给 Claude。ReAct 继续使用原来的
`framework = "agentscope"`。V1 Async 的并发、候选评估及原子 trial 仍由原平台处理。
桥接不使用 HTTP Authorization 值；不要把真实 API key 填到本地桥接配置中。

当前 runner 会校验配置，切换后应创建新实验，不能用新模型续写已有 DeepSeek 运行。
共享配置文件的变更也会影响旧运行的校验；有旧实验要恢复或测试时，保留其原配置。
本次没有启动桥接常驻服务，也没有修改这些配置文件。

## 调用和工具边界

```text
现有 HTTPModels / AgentScope OpenAIChatModel
  → POST /v1/chat/completions
  → 独立 CLI 进程：完整研究提示 + 调用方提供的历史
  → 完整 assistant 消息
  → OpenAI 格式文本 / tool_calls
  → 原 AgentScope Toolkit / runner 执行工具和评估
```

- 接口接受普通 JSON 或 `stream=true`。SSE 在完整模型消息到齐后一次发送，避免把部分工具
  参数交给 Agent；不提供实时逐 token 输出。
- 每次请求独立启动 CLI，不共享会话、消息历史或候选状态。历史工具调用和结果序列化为
  JSON 对话记录交给 CLI；服务重启和并发请求不需要额外恢复状态。
- 工具 schema 通过临时的回环 MCP 服务交给 CLI，仅接受平台现有的 `evaluate`、
  `get_context`、`library_search`、`library_get`、`library_list`、`library_stats`。
  `evaluate` 不标为只读。MCP handler 只等待，不执行工具，也不产生伪造的工具结果。
  收到同一 assistant 消息的全部内容块和 `message_stop` 后，桥接关闭 CLI 并返回整批
  `tool_calls`；原平台按原顺序处理候选。CLI 不在内部另跑研究工具或搜索循环。
- 关闭 CLI 内置工具、skills/命令、普通配置来源、hooks、自动记忆、附件展开和自动压缩；
  只提供显式 MCP schema。CLI 在临时空目录运行，使用完整替换的系统提示文件。
  不开放 Bash、文件、网络搜索或项目数据访问工具。
- 请求超时、调用方断开或桥接退出时，清理该请求的 CLI 进程组和临时目录。
  不保存 CLI 会话或打印请求、回复、请求头、凭据及 CLI stderr。
- 子进程清除 `ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、自定义 API 地址与
  Bedrock/Vertex/Foundry 路由环境选项，保留 CLI 自身登录目录。组织管理策略仍可能影响
  CLI；模块不会绕过它们，也不修改用户的 Claude 设置文件。

## 支持范围和比较口径

仅实现本项目需要的 `/v1/chat/completions` 文本和函数工具子集。
`n` 只能为 1，`tool_choice` 只支持 `auto` 和 `none`。多模态、强制工具选择、结构化输出
扩展及其他路由明确拒绝。`/embeddings` 不受支持：AlphaProbe 只切换 Chat，原 embedding
模型、地址和认证应保留。

CLI 没有在此桥接中映射 temperature、top_p、max_tokens/max_completion_tokens 或
parallel_tool_calls。为兼容现有客户端，这些字段可传入，但使用 CLI 自身的生成设置；
平台工具的执行顺序仍由原 Toolkit 控制。方法里的采样温度和输出额度不能视为已生效。
因此这是 **Claude CLI 后端适配**，不能据此宣称与原 OpenAI 格式服务具有相同采样口径。
需要严格控制这些参数的实验应继续使用原 API 后端。

桥接不额外重试，HTTP 调用方沿用自身的重试和请求记账；CLI 仍可能在内部重试网络请求，
不能把桥接 HTTP 请求数当作底层模型的精确推理次数。输入 token 计入 cache read/write，
同消息多个内容块不重复累加，输出用最终 `message_delta` 的计数；缺失用量返回 null。
这些值是已报告的 token，不代表订阅账单金额或剩余额度。

已知认证、计费、限流和上下文错误分别映射为 HTTP 401、402、429 和
`400/context_length_exceeded`。不完整流、异常退出和未知 CLI 错误返回 502；超时为 504。
错误不回传 CLI 的原始输出。截断、空回复及未开放的工具都不能作为候选提交。

## 来源和验证

实现思路参考同一工作区的 `ChaoticTrader_Future/agent/claude_cli.py`，参考仓库提交为
`7f772f2aaa5006d0e297bf205323ff8d67f4d071`，读取时的本地工作树也作为参考。
参考位置未发现许可证文件；本目录独立实现协议适配，不导入或分发该项目的 Agent、存储或
数据代码。区别是本模块提供 HTTP 接口，每次只取一个完整模型消息，不复用长驻 CLI 会话，
也不支持参考实现的内置 WebSearch/WebFetch。

协议依据是官方 [CLI 参数](https://code.claude.com/docs/en/cli-reference)、
[程序化调用](https://code.claude.com/docs/en/headless)、
[消息流顺序](https://code.claude.com/docs/en/agent-sdk/streaming-output) 和
[环境参数](https://code.claude.com/docs/en/env-vars)。不能用 `--bare` 替换这里的隔离选项，
因为该模式不读取订阅登录凭据。

```bash
uv run pytest -q integrations/claude_cli
pwsh -File scripts/check.ps1
```

模块测试不进入主项目默认 testpaths，整目录删除后，主项目检查命令保持可用。
测试使用合成 CLI 和本机回环 HTTP/MCP，包括现有 HTTPModels、原生 AgentScope ReAct 的
整批工具调用及状态恢复、并发隔离、未知用量、失败脱敏和超时/断开清理。

移除时先停止本模块进程，把后续实验的 Chat 配置设回原 API，然后删除此目录即可。
不需要改 `src/alpha_atlas`、`pyproject.toml` 或 `uv.lock`；保留历史实验产物。
