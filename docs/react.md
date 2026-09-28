# 单 agent ReAct baseline

适用 [全方法统一复现规范](method_reproduction.md)；本方法定位为 AgentScope 自建 LLM 基线。

一个 AgentScope ReAct agent 在固定评估预算内持续探索高质量、非冗余的因子。使用现有 DSL、
评估和因子库，不引入 embedding、额外搜索算法或算子创建工具。

## 运行与配置

```powershell
uv run atlas run --asset futures --fold fold1 --method react --attempts 100
uv run atlas resume <run_dir>
uv run atlas test <run_dir>
```

`configs/react.toml` 使用 AgentScope 的 OpenAI-compatible model，默认连接
`http://127.0.0.1:8001/v1`，模型 `Qwen/Qwen3-8B-AWQ`，
temperature 0.5，请求超时 120 秒。凭据只从 `ALPHA_ATLAS_LLM_API_KEY` 环境变量读取。
普通回复最多 1536 tokens；AgentScope 压缩沿用该 model 配置。`summary_output_tokens` 仅用于
兼容旧 HTTP 路径；只限制生成长度，不限制输入上下文。
公共 `configs/benchmark.toml` 中的 `reference_library = "futures_cta"` 开放基础定义检索；
设为空字符串对所有方法关闭，保留有/无基础库实验维度。设置进入已有 rules 与冻结校验，
不预先向本次运行库写入成员；`configs/react.toml` 不再单独配置参考库权限。
模型 context size 通过 `configs/react.toml` 的 `context_size` 传给 AgentScope；AgentScope 按
比例触发上下文压缩，项目不读取原始行情来决定压缩。

## 提示词和工具

全部 6 个工具的实际输入输出、调用约定及错误示例见
[ReAct 工具检查与示例](react_tools.md)。示例使用隔离的合成期货数据验证 JSON 分发、
只读查询、评估与准入链路，不调用模型服务或行情 API，不代表真实因子表现。

模板在 `src/alpha_atlas/methods/react.py` 的 `SYSTEM`；运行时一次性补充资产、频率、目标、
主指标、全部允许字段、当前完整算子签名、DSL 与准入规则。主指标、门槛从
当前 session 获取，日期、fold、universe、路径和运行/快照 ID 不进入提示词。
平台内部 `SearchContext` 保留预算用于调度；所有方法的模型可见 `get_context()` 由
`SearchSession.query` 统一提供，一次返回资产、频率、目标、主指标，以及 `fields`、
`operators`（完整定义和含义）、`expression_rules`、`evaluation_rules`、`reference_library`。
首轮提示词和工具返回复用同一份元数据，不含实时预算、日期、运行身份或 test 数据。
`remaining_attempts` 仅在评估的 `tool_result` 中返回；只读观察消息不附加动态预算。
评估前后 context 与固定提示词不随预算改变，减少对可复用前缀的影响；是否命中 KV cache
由实际推理服务决定，不以格式检查代替缓存性能实测。
系统提示词按八节组织：任务与流程、工具协议、研究设置、允许字段、表达式规则、评分与
准入、参考库、算子目录。JSON 请求示例使用代码块，每个工具单独列出签名、用途和返回
内容；明确示例的代码围栏不能进入模型实际回复。
基本信息与数值约束使用 Property / Value / Meaning 表格，说明目标周期的单位、初始
预算、严格与非严格门槛等。嵌套属性保留完整路径（如 `target.horizon_bars`）。
字段按主合约、`_p1`、`_p2` 分组，使用 Expression / Meaning / Coarser suffixes 表格，
逐行列出实际允许的字段及周期后缀，解释价格、成交量、成交额、持仓量和剩余日历天数。
自定义字段缺少定义时明确说明，不猜测含义。时间对齐、数值计算与评估规则按主题拆成
条目；算子目录前解释参数类型、作用域、历史需求和别名。工具请求与结果仍使用 JSON。
格式变更用于新建运行；恢复时继续使用 checkpoint 中保存的固定提示词。
算子清单使用 Markdown 四列表格：Signature、Meaning、Scope / output、History / constraints。
签名与含义相邻展示，最后一列保留历史需求、最小窗口、别名及自定义参数信息。
完整提示词预览直接渲染该表，不放在代码块内；传给模型的原始 system 同样使用 Markdown 表格。
60 个基础算子的说明与底层注册表、生成的算子文档共用 description，
包含计算定义及关键约定（例如简单收益、样本方差、极值距离和缺失条件处理）。

可直接查看一次运行实际发送的固定系统提示词：

```powershell
(Get-Content <run_dir>/checkpoint.json -Raw | ConvertFrom-Json).method_state.system_prompt
```

AgentScope 负责工具 schema、工具调用解析和循环；一个模型回复可以包含多个工具调用，
只读查询可并发执行，评估调用按平台预算和原子 trial 顺序处理。模型服务须支持
OpenAI-compatible tools API；vLLM 部署时需开启对应的 auto tool choice/parser。

```json
{"tool":"evaluate","arguments":{"expression":"TS_MEAN(RETURN($close,1),12)","hypothesis":"短期趋势延续"}}
```

工具包括 `evaluate(expression, hypothesis="", name=null)`、`get_context()`、
`library_get(factor_id)`、`library_list(offset=0, limit=20)` 和 `library_stats()`。
另有 `library_search(query="", source="all", offset=0, limit=10)`：source 可选 `run`、
`reference` 或 `all`，按空白拆分的关键词须全部匹配名称、公式或描述，忽略大小写。
空关键词用于浏览；每页最多 20 项。run 仅检索本次运行已入库成员，返回 train/val 报告；
reference 按需加载基础公式，原日窗口数按原生 Bar 解释（bars_per_day=1），返回来源、
版本、公式和 missing_fields，没有绩效或自动准入。跨频改写仍使用显式后缀。
库列表每页最多 100 项，返回候选与主指标摘要；详情包含 train/val/raw 指标、方向和覆盖率。
ReAct 不再暴露 `get_fields`、`list_operators`、`get_operator`、`get_expression_rules`、
`get_evaluation_rules`，调用这些旧名称会返回未知工具错误。字段、规则和库查询的实现
已集中到 `SearchSession`，AlphaPROBE 和符号基线使用同一接口。`get_context` 返回参考库
配置，不触发公式加载或入库。各方法的候选均经 runner 调用公共 evaluate，保留原子 trial、
预算、准入和恢复流程；公共 evaluation_result 生成一致的模型反馈。

模型从第一笔开始自行生成候选。Runner 原子提交评估，随后方法将公式、完整指标、准入或
失败原因、最近邻相关性和当前剩余预算加入反馈。DSL 错误、重复及其他拒绝也扣一次预算。
查询、格式纠正和摘要不扣评估预算。预算为零后直接冻结，不再请求新因子。

期货使用 `time_series_weighted_pearson_ic`：训练方向由 train 主指标确定，方向统一后的
train/val 主指标都须严格 `>0.005`；与全部已入库成员在验证集共同有效行上的绝对
Spearman 相关性须严格 `<0.7`。原有最低覆盖率 0.8、共同有效行数等质量门槛保留。
平台在 evaluate 中原子提交符合条件的因子，并向 agent 返回 `accepted`、`reason`、
`max_abs_corr`、`nearest_factor`、`comparison_complete`、`library_version` 和 `trial_index`。
无需 agent 另行申请入库；参考定义也必须经过这些门槛。

远月与多周期完整验收配置：

```powershell
uv run atlas run --asset futures_curve --fold fold1 --method react --attempts 100 --allow-incomplete `
  --fields open,high,low,close,volume,amount,open_interest,days_to_maturity,open_p1,high_p1,low_p1,close_p1,volume_p1,amount_p1,open_interest_p1,days_to_maturity_p1,open_p2,high_p2,low_p2,close_p2,volume_p2,amount_p2,open_interest_p2,days_to_maturity_p2
```

`--allow-incomplete` 显式接受已复查的两项缺报价，仍由 MarketData 隔离；不将 manifest
改为 complete。完成后 `uv run atlas test <run_dir>`，test 结果不反馈给搜索 agent。

## 上下文压缩

AgentScope 的 `ContextConfig` 在达到模型 context 比例阈值时自动压缩，保留最近消息并用
结构化摘要替换较早上下文；压缩调用沿用同一个 AgentScope model。工具调用及结果按配对
保留，摘要和消息状态随 agent state 一起写入现有 `checkpoint.json`。
连续 10 次模型回复没有产生有效格式的评估请求，同样明确失败，不构造假候选消耗预算。

## 记录与恢复

固定提示词、AgentScope agent state、待评估请求和模型用量保存在现有 `checkpoint.json` 的方法状态中。
已落盘模型响应在恢复时复用；已提交 trial 不重复评估。
模型回复到落盘之间异常退出可能重发该模型调用，用量不确定按现有规则记为 unknown。
对话压缩后保留摘要与最近循环，原始数值证据仍可从原子 trial 和因子库查询。

控制台通过 SSE 实时显示服务端返回的思考与回复，支持 `reasoning_content` / `reasoning`
及跨消息片段的 `<think>` 标签。未返回思考的模型只显示回复，不补造思考内容。AlphaProbe
使用同一流式 HTTP 客户端。流中断不会把部分回复提交为候选；断流、连接错误、超时和
临时 HTTP 错误按 [统一规范](method_reproduction.md) 最多额外重试 3 次，等待 1／2／4 秒。
搜索与摘要请求都遵循该规则，重试计入请求数，失败用量记 unknown；耗尽后可按原流程恢复。
评估时单独展示完整表达式；结果用无外框的对齐列显示原始训练 IC 和定向验证 IC。
星号标注主指标，底部注明其完整名称；只有实际出现缺失值时才显示“— 表示无法计算”。
模型通过 `name` 提供简短因子名，与候选一起保存；成功提交 trial 后才显示 `[因子入库] 名称`。
无名候选使用表达式作为显示名称，并附短 ID。所有进度和流式文本写入 stderr，stdout 保留 JSON，
`--quiet` 同时隐藏进度和模型流。统一报告列出
`chat_requests`（含摘要）、`summary_requests`、token 用量、`context_overflows` 和 `compressions`。
配置和冻结环境检查沿用平台机制；源码指纹仅记录来源，不阻止恢复。
不额外增加状态文件或哈希。

## 验收记录

2026-09-10 完整检查 320 项通过。真实期货运行
`futures-all-fold1-react-42-e970bc5f44` 在 94.26 秒内完成 5 次评估，2 个成员准入并冻结。
服务实际返回过一次上下文超限，同模型摘要压缩后继续完成剩余预算；未调整 vLLM 容量。
该记录属于 AgentScope 迁移前的 HTTP/JSON 路径；另有合成数据覆盖冻结 OOS、异常和中断恢复，
不以本次 val 结果代表 OOS 表现。
