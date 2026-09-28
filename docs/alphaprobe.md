# AlphaPROBE：统一协议下的搜索算法适配

适用 [全方法统一复现规范](method_reproduction.md)；以下保留本方法具体算法与偏离说明。

实现名 `alphaprobe_atlas_v1`，入口 `--method alphaprobe`。这是使用真实 LLM 和语义
embedding 接口的搜索实现；自动测试用模拟响应验证算法和运行协议。旧版人工模板初始化
已有真实模型运行产物；本次默认 LLM 冷启动尚未完成市场实验。不声称复现原论文的实验
收益、原始算子数值或动态组合。

参考：[AlphaPROBE 论文](https://arxiv.org/abs/2602.11917)，以及
[公开实现 872299d](https://github.com/gta0804/AlphaPROBE/tree/872299dc9841f3c3096a2786fe976a2e2e1ad23f)。
搜索逻辑位于 `src/alpha_atlas/methods/alphaprobe.py`，不引入 Qlib、Torch、图数据库或新的因子身份。

## 保留的搜索机制

1. `initial_expressions = []` 默认无预置种子：LLM 根据公共研究 context，通过现有三阶段
   流程生成 `min(offspring, remaining_attempts)` 个独立根候选，默认最多 5 个，提示要求
   覆盖不同金融机制。整批逐个评估后再进行图检索，不注入原来的五个价格／成交量模板。
   有种子实验可显式提供 DSL 列表，公式文本作为初始语义描述。所有提交均计入尝试预算。
2. 根候选（LLM 生成或显式配置）只需评估成功、训练期质量有限且非零；不受
   `min_train_quality` 限制。有父节点的演化子代仍需达到该门槛，默认 `0.006`。
   图节点引用评估器给出的规范化 AST 和已有 factor ID。相同计算身份不重复建点或改父节点，
   因而不会形成循环。每点最多一个生成父节点；多棵演化树共同构成 DAG。
3. 按训练期质量保留最多 `pool_capacity` 个活跃父因子；被挤出活跃池的节点及祖先关系保留。
   方法内部图／活跃池与正式因子库不同，val 拒绝不抹除训练搜索历史。
4. 每轮计算先验与似然，合并叶／非叶节点进行全局 top-k 排序。并列时使用运行 seed 的 RNG。
   选中的节点增加检索次数，然后依次生成子代。全部得分为零时仍按相同并列规则推进。
5. Analyst 根据完整祖先路径提出多种修改策略；Execution 逐一生成我们的 DSL；Validator
   检查和修正字段、类型、因果性、历史与复杂度。每阶段一次 Chat 请求；最终数值执行和
   准入仍由 Atlas 编译器及评估器完成，LLM 的判断不能绕过平台校验。
6. 子代反馈更新图；下一轮检索使用新结构。空图时 LLM 可提出新的根假设，不能用固定假因子
   或关闭语义项冒充成功生成。

设 `q(F) = abs(train IC)`。先验为：

```text
prior(F) = sigmoid((q(F) - mean(q)) / std(q))
           × (1 - depth_penalty)^depth(F)
           × (1 - retrieval_penalty)^retrievals(F)
```

叶节点似然是三项乘积：

- 数值：`1 - abs(mean(Corr(F, other)))`，保留论文绝对值在均值外的定义。
- 语义：`sigmoid(1 - mean(cosine(embedding(F), embedding(other))))`。
  文本是因子的金融解释；向量归一化后保存在方法状态中，不会每轮重新请求已有 embedding。
- 结构：与其他活跃表达式的 AST 编辑距离除以两个 AST 节点数之和，再取均值。

非叶节点似然为：

```text
gain = mean((q(child) - q(parent)) / q(parent))
likelihood = max(0, gain)
             × (1 - mean(Corr(parent, child)))
             × (1 - mean(Corr(child_i, child_j)))
```

仅一个子代时最后一项为 1。质量标准差和增益分母采用 `1e-12` 下界；单节点尚无参照池时
似然为 1。必要相关性不可定义或共同有效样本不足时似然为 0，不把未知关系当作高多样性。

## 与上游的适配边界

| 项目 | 本项目的实现 |
| --- | --- |
| 质量 | 统一资产配置的训练期主指标 IC 绝对值，替代原文训练 ICIR；val/test 不参与检索评分 |
| 叶／非叶打分 | 采用论文乘法公式及全局 top-k；不采用公开代码的 `1 - corr_pc * corr_cc` 或半数叶节点配额 |
| 子代增益 | 保留百分比增益；非正平均增益截为零，避免负似然 |
| 图准入 | 根需成功评估且训练质量有限、非零；子代另需达到训练质量门槛；正式准入仍使用平台规则，不复制上游的父子改善／相关性组合过滤 |
| 活跃池 | 训练质量 top-50（可配置），全部历史留图；不替换正式因子库成员 |
| 初始库 | 默认 LLM 冷启动，或明确配置种子；官方入口预置 29 个表达式，本地不自动导入；根免子代门槛不等于免费正式入库 |
| 表达式 | 全部走 Atlas DSL、算子、正整数窗口、合约分组和复杂度边界；无上游算子兼容层 |
| 结构距离 | 基于规范化 AST，包含窗口和标量参数；交换律算子允许交换操作数，不沿用上游忽略大多数窗口差异的规则 |
| 生成 | 三个显式角色请求，祖先信息和输出保存；公开代码的合并提示词不照搬 |
| 组合与测试 | 不引入动态 Mega 因子、交易回测或搜索期 test 日志；冻结后统一审计全库 OOS IC |

## 训练期相关性接口

`session.factor_correlations([(factor_id_a, factor_id_b), ...])` 只接受本运行已经评估且
具有规范化计算表达式的因子，不接受外部表达式或另一个 run 的 ID。
返回不可变 `FactorCorrelation(left, right, value, n_obs, aggregation, split="train")`。

- A 股：训练期每日横截面 Pearson，逐日等权。
- 期货：同品种全部合约的训练样本合并后计算 Pearson，再按整个训练期间的品种
  `sqrt(sum(amount))` 加权，沿用平台主指标的品种权重与有效性规则。
  图节点质量也使用主指标——训练期 SQRT 成交额加权 Pearson IC 的绝对值；其他 IC 仅报告。
- 共同有限值、每个相关分组至少 5 个观测，整体有效观测不少于统一规则 `min_corr_overlap`。
- 只在训练评分范围内计算，保留既有 eligible、标签可用性与 split 端点过滤。
  不输出因子数组或任何收益标签。
- 因子间统计不生成新 trial，不重新准入；耗时计入运行活动时间。Runner 在 ask 前计算，
  因而候选生成耗时也包含这部分检索准备。恢复前已经记录的统计保留在方法状态中。
- 训练因子值采用运行内、按字节限额的内存缓存，仅此方法启用主动保留；缺失时仅重建
  请求的因子，读取的仍是评估器已有 MarketData 面板，不访问供应商或 Parquet。
  不增加缓存校验和或成员扫描。训练缓存额度与现有评估缓存额度各自计算，单因子可超过额度。

AlphaProbe 在 ask 阶段通过绑定的 `session.factor_correlations(correlation_pairs())`
请求训练统计，不再通过 context 传递。`EvaluationReport.canonical_expression` 提供编译后的计算树，
复用原 factor ID，不增加身份体系。

## 配置与运行

编辑 `configs/alphaprobe.toml` 的 `chat_model`、`embedding_model` 和两个 `*_base_url`。
当前 Chat 为本地 `http://127.0.0.1:27483/codex/v1` 的 `gpt-5.5`；Embedding 为
`http://127.0.0.1:8003/v1` 的 `Qwen3-Embedding-0.6B`。缺少模型配置时在加载行情前报错。
模型可使用不同服务；模型名、地址、参数和密钥环境变量名记录在原有 run 配置中。
终端每次“模型请求”同时打印类型、实际配置的 `MODEL` 名称和请求次数。

客户端使用标准库发送兼容请求：

- `POST /chat/completions`：`model`、`messages`、`temperature`、`max_tokens`。
- `POST /embeddings`：`model`、文本数组 `input`、`encoding_format="float"`。

参考 [Chat API](https://developers.openai.com/api/reference/resources/chat) 与
[Embeddings API](https://platform.openai.com/docs/api-reference/embeddings/create)。
所选服务需支持上述请求参数。API key 仅从配置所指向的外部环境变量按请求读取；不读取项目
`.env`，不把密钥写入配置、日志或检查点。无鉴权的本地服务可不设置对应环境变量。

```powershell
uv run atlas run --asset ashare --universe union1800 --fold fold1 --method alphaprobe --seed 42 --attempts 100
uv run atlas resume artifacts/runs/<run_id>
uv run atlas report artifacts/runs/<run_id>
uv run atlas test artifacts/runs/<run_id>
```

默认输入沿用 Runner：`adj_close`、`volume`、`amount`（A 股）；需要其他输入时使用
既有 `--fields`。LLM 看见本次允许的字段与 60 个基础算子目录。此方法不自行注册新算子。
首批 LLM 根候选默认最多五个，各占一次尝试，预算不足时只生成剩余额度允许的数量。
非法、重复、零质量或不可定义的根照常计预算，不免费补齐。整批结束后有图则开始演化；
若仍为空图，沿用现有空图生成流程，后续提交仍扣预算。有图时不周期性重启或补根。

“无种子”指无预置初始因子，不等于无参考信息：公共表达式示例仍保留；参考库是否开放
沿用 `benchmark.toml`，与 MCTS-LLM 等方法保持统一权限。开放时应标注为“无预置种子、
允许公共参考库”。显式种子也须逐个评估，不能直接成为正式库成员。
旧五模板运行保留，其结果属于有预置种子组。无种子对照实验应新建运行，不能把旧检查点
续跑的结果改标为无种子。源码变化不再阻止续跑或 OOS；配置和检查点规则仍校验，
已提交 trial 和历史来源记录不回写。

每次生成数量为 `min(offspring, remaining_attempts)`。非法表达式和重复表达式正常消耗
尝试；格式错误的模型响应以空 DSL、`alphaprobe_generation_error` 候选记录一次编译失败，
错误与原响应留在方法状态中。不执行自动重试或无限修复。HTTP／embedding 错误使运行失败，
修复外部服务后使用同一配置恢复。

## 检查点、成本与复现限度

沿用 `checkpoint.json`，保存图、活跃池所需信息、RNG、相关统计、embedding、检索分数、
祖先路径、三阶段原响应、待处理子代及反馈位置。每次模型调用前记录请求计数，响应收到后
保存已完成阶段。一个批次只生成一次，子代逐个复用原子 trial 提交机制；tell 不调用模型。

进程中断后，已保存阶段不重发，已提交 trial 不重评、不重复扣预算或入库。
若供应商已接收请求但响应尚未保存，恢复可能再次调用；这部分计入
`unknown_usage_requests`，不声称外部请求恰好执行一次。已有 embedding 的阶段可恢复；
返回但尚未写入节点的 embedding 仍可能重新请求。模型服务器的确定性也不由本地 seed 保证。

报告显示请求数、已知 prompt/completion/embedding token、未知用量请求数与图／检索规模。
Chat 和 embedding 都按 [统一规范](method_reproduction.md) 对断流、连接错误、超时及
临时 HTTP 错误最多额外重试 3 次，等待 1／2／4 秒。重试计入对应请求数，失败用量记
unknown，不增加候选评估次数；耗尽后沿用阶段恢复。
目前没有价格换算、token 硬预算或活动总时长硬上限；`max_output_tokens` 是每次 Chat 的
输出限制，`timeout_seconds` 是单次 HTTP 请求超时，不是实验总预算。
方法配置沿用现有 run 指纹保护，恢复与 OOS 不允许悄悄改变配置。

与现有四个有限文法基线比较时，必须披露其搜索空间、初始化与模型费用差异。相同算子目录
不意味着每种搜索器都能探索相同组合，不能据此宣称已经完成严格控制变量的论文比较。

## 验证

`tests/test_alphaprobe.py` 使用合成面板和确定性模拟模型，覆盖：论文评分手算、退化分数、
语法距离、训练／验证隔离、期货聚合、缓存复用与按需重建、未知重叠、图／正式准入分离、
重复身份无环、默认 LLM 冷启动、显式种子、根／子代门槛、无效根计费、空图生成、
三阶段及批量预算、冷启动及演化的模型阶段／trial 提交／tell 后恢复、配置冻结、
OOS 不回写搜索状态、模拟 HTTP 协议、错误响应计数和凭据不进入错误信息。

完整项目检查：`pwsh -File scripts/check.ps1`。测试结果仅证明实现与协议行为，不能作为
真实市场搜索效果或模型供应商兼容性实测的替代。

## 模型只读查询

LLM 使用与 ReAct、符号基线同源的公共研究 context：asset、frequency、target、metric、
fields、operators、expression_rules、evaluation_rules、reference_library。
不传日期区间、fold、universe、运行/快照 ID 或实时预算。最近一次公共评估结果单独放在
evaluation_result 中，其中包含 remaining_attempts；沿用现有方法状态保存和恢复该反馈。
三阶段生成沿用 JSON 响应协议；模型可先返回：

```json
{"queries": [{"name": "get_context", "arguments": {}}, {"name": "library_stats", "arguments": {}}]}
```

复用 SearchSession.query 的 get_context、library_search、library_get、library_list、
library_stats 五个只读工具。参考库开关来自 configs/benchmark.toml，所有方法一致。
评估使用同一个平台 evaluate：三阶段输出候选后由 runner 原子提交，查询阶段不私自评估。
平台把结果放入下一次请求的 query_results；查询完再返回该阶段原有的策略或候选 JSON。
每阶段最多 8 轮查询、每轮最多 16 项，非法查询返回错误，持续超限按生成失败记录。
这是现有 JSON 对话上的专用查询协议，不依赖服务端原生 function calling 或任意 Python 执行。
查询也计入实际模型请求/token 成本，已完成查询保存于现有 batches/checkpoint；恢复继续使用
已保存结果。仅绑定既有 session，不增加文件、工具管理器或新的身份校验。

工具权限和评估口径统一，搜索策略仍各自独立：AlphaPROBE 使用冷启动或显式种子、图检索和
三阶段生成，符号基线保留有限语法。开放参考库表示各方法可以查询，并不强制自动使用种子。

平台没有窗口或累计历史上限；正整数、算子最小窗口、完整有效历史和复杂度约束保留。
冷启动窗口由 LLM 根据公共 context 提出；显式种子使用给定窗口，不再自动按目标 horizon
构造初始模板。
