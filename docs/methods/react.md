# ReAct 搜索基线

入口 `--method react`，实现位于 [react.py](../../src/alpha_atlas/methods/react.py)。
使用 AgentScope 单 Agent 的工具循环，目标是在尝试预算内积累合格且非冗余的因子。
这是自建 LLM 基线，不预置种子、不需要 embedding，不开放模型创建算子。

## 运行与配置

```powershell
uv run atlas run --asset futures --fold fold1 --method react --attempts 100
uv run atlas resume artifacts/runs/<run_id>
```

当前配置为 `deepseek-flash`、`https://api.deepseek.com/v1`，密钥环境变量为
`OPENAI_API_KEY`，输出上限 65,536 token，context 压缩容量为 131,072 token。
Kernel 完整八字段运行使用：

```bash
uv run atlas run --asset futures_kernel_5m_full --universe all --fold fold1 \
  --method react --seed 42 --attempts 1000
```

不指定 `--fields` 时使用该资产的全部可用数值字段；已有四字段实验仍对应各自原快照。

[react.toml](../../configs/react.toml) 定义服务、模型、温度、输出长度、超时和密钥环境变量名。
服务须支持 OpenAI-compatible tools API。凭据从外部环境变量读取，不写入仓库或日志。
参考库权限统一由 `benchmark.toml` 控制，规则见[方法复现规范](../method_reproduction.md)。

## 工具循环

1. SYSTEM 模板一次性注入公共 context：研究目标、字段、完整算子、表达式与评估规则。
2. 模型通过标准 tools API 查询或提交候选。一轮可返回多个调用，候选由 runner 顺序原子评估。
3. 公共反馈包含指标、准入／拒绝原因、最近相关程度及剩余尝试数，再进入下一轮。
4. 预算耗尽后冻结。连续 10 次回复没有有效格式的评估请求时明确失败，不构造假候选。

完整工具定义见[研究工具](../research_tools.md)，固定提示词不注入日期、运行身份或动态预算。
已发送的系统提示词可从现有检查点读取：

```powershell
(Get-Content <run_dir>/checkpoint.json -Raw | ConvertFrom-Json).method_state.system_prompt
```

AgentScope 按 context 比例压缩旧消息，保留最近消息和工具配对；摘要使用同一个模型，
压缩／查询不扣评估预算，但计模型成本。`summary_output_tokens` 仅用于旧 HTTP 路径。
压缩使用项目的研究摘要提示词，关闭自动截断回退；摘要失败显式报错。
SDK 仅预先允许 `evaluate` 排队，实际预算、数值执行与准入仍由 runner 原子提交。

DeepSeek 使用 SDK 的 `DeepSeekChatFormatter` 保留工具往返所需的 `reasoning_content`，
输出上限发送为服务接受的 `max_tokens`，HTTP 超时沿用方法配置。
多轮请求复用一个事件循环，结束时关闭客户端和循环，避免跨轮复用连接时报事件循环已关闭。

## 状态与失败

`checkpoint.json` 保存固定提示词、agent state、待评估请求、摘要和模型用量。
已保存响应与已提交 trial 按公共规范复用；响应落盘前的中断可能重发，未知用量如实记录。
原始数值证据保留在 trial 中，摘要不替代正式因子库或评估记录。

临时网络错误按[公共重试规则](../method_reproduction.md#预算失败与恢复)处理。
原生 AgentScope 路径在单次模型响应内覆盖建立连接和消费流的全过程，最多额外重试 3 次，
等待 1/2/4 秒；关闭 SDK 内层重试，避免叠加请求次数。完整响应才交给 Agent 执行工具，
断流丢弃当前片段，仅重发当前模型请求，已完成的工具调用不重放；Ctrl+C 和参数错误不重试。
模型请求数按实际尝试累加，未获得完整 usage 的请求记为未知用量。
模型流和进度写 stderr，stdout 保留运行 JSON；进度采用单行格式，交互式终端在同一行刷新回复并保留阶段状态。
长回复只显示末尾片段；独立 reasoning 字段与 `<think>...</think>` 内容不显示。`--quiet` 隐藏进度与回复流，断流片段不能作为候选。

## 验证

[test_react.py](../../tests/test_react.py) 和
[test_research_tools.py](../../tests/test_research_tools.py) 使用模拟模型与合成数据验证
工具批次、信息边界、压缩、预算、阶段恢复和冻结 OOS。历史真实流程验收见
[实验记录](../records/experiments.md)，迁移前 HTTP 路径的结果不代表 AgentScope 已完成市场验收。
原生 AgentScope 路径另用模拟 SSE 服务覆盖连续两轮工具调用、DeepSeek 参数与思考内容保留、
跨轮事件循环复用和客户端关闭，以及中途断流后的请求重发、候选不重复、有限重试和终止错误；
测试不使用真实服务或凭据。
