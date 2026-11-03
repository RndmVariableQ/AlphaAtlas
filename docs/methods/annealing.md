# AlphaAtlas 三 Agent 退火搜索

入口 `--method annealing`，实现标识 `annealing_llm_atlas_v1`。方法由 Atlas Explorer、
Research Analyst 和 Factor Miner 三个角色组成，退火搜索的状态是当前研究 Region。

研究状态使用 `research_plan_state`，其中 `research_plan_index` 标识本次计划执行；
`factor_trial_records` 保存原计划、方向、区域、引用、计划编号及评估尝试编号，
以 `factor_trial_index` 关联一次原子 trial，不再保存候选公式、评估结果和准入状态副本。
`completed_research_plan_count`
只统计已结束计划数量，异步完成顺序不保证与计划编号一致，不能用该数量代替计划身份。
旧检查点的 `steps`、`round_state`、`round_index`、计划内 `index` 在既有加载入口读取；
旧记录中的候选、评估和准入副本在内存加载时丢弃，不据此覆盖 trial，也不重写历史文件。
原始模型输入、对话和证据正文保持保存时的内容。V1 和 V1 Async 共用这一约定。

Analyst 选择测量路线、字段、特征周期、窗口、对照和阈值；Miner 按计划实现候选并使用
已有反馈提出不同候选。角色提示不再重复说明平台准入职责。基础字段示例、基础频率和
可用高周期由公共 context 动态说明；共享 DSL 示例只说明语法，不作为测量证据。

区域统计、完整计划证据和 Analyst 历史反馈通过 `SearchSession.get_trial` 按编号读取
原子 trial；这只是方法内部能力，不新增 Agent 工具或数据权限。创建一份 Explorer 输入时，
同一条 trial 的读取结果在该次组装中复用，之后不保留额外缓存。完整地图和历史结果索引
仍需汇总相关全部记录；单独展开计划只读取该计划的 trial，不扫描 trial 目录。
缺失、损坏或编号不匹配的记录明确失败，不回退到旧副本、不重新评估。
恢复已有输入、对话和 `sources` 则沿用保存内容，不重新读取并替换模型已见证据。

## 一轮研究

1. Explorer 收到当前 run 的完整 Region 地图和公共研究 context。每个 Region 附有本 run 的
   尝试数、成功评估数、Valid 覆盖达标数、入库数、拒绝原因、主指标分布和少量支持／反向
   trial 引用；不合并其他 run，也不包含 OOS。期货使用配置的 weighted Pearson IC：Train
   汇总绝对值，Valid 已按 Train 方向同向化。Explorer 可以选择已有 Region，也可在已有
   continent 下提出新 Region；高温阶段更鼓励广度探索，低温阶段更鼓励围绕当前 Region 深挖。
2. Analyst 将方向变成一个受字段范围约束的可执行计划，不可改写 Explorer 的 Region 或方向。
   同时接收上一计划正式入库候选中按主指标 `abs(val IC)` 排序的前 5 个，作为研究反馈。
   `allowed_fields` 填基础字段名；所需的已完成高周期写在测量计划和 Miner 指令中。
3. Miner 在一个持续对话中按计划逐个生成候选，最多尝试 5 个。每个候选都由 AlphaAtlas
   runner 单独评估；评估结果会追加到 Miner 对话，供后续候选参考。`--attempts` 仍只按候选
   计数。
4. 每轮结束后，使用本轮候选正式入库比例作为 Region reward。分数较高或相同的方向成为
   当前 Region；较低的方向仍按 `exp((新分数-当前分数)/T)` 概率接受。拒绝只让后续搜索回到
   原 Region，不撤销已完成的评估或入库。
5. 温度按总候选预算几何下降，默认 `0.25 → 0.02`。高温时较容易接受其他方向，扩大覆盖；
   温度降低后更稳定地留在有入库证据的区域。

Analyst 的候选反馈仅包含服务端已正式入库且覆盖率等门槛合格的因子；按运行主指标的
方向化验证 IC 绝对值降序选择，最多 5 个。期货主指标为成交额加权 Pearson IC；其他资产
使用各自配置的主指标。该筛选只决定 Analyst 可见的反馈，不改变平台准入。

Explorer 地图中的 Train/Valid 分布来自本方法引用的 run 内原子 trial 记录；分布包含所有评估
成功且 Valid 覆盖达标的候选，入库数另列，不以入库结果筛选样本。Train 分布按 Train 主
指标符号统一方向，Valid 使用评估报告中已按同一 Train 方向调整的值。证据标签只描述本轮
样本；样本不足时保留不确定性，不把相关性解释为因果。Explorer 引用只能来自地图展示的
trial 样例。AlphaAtlas 当前把这份精简全图直接放入 Explorer 输入，没有单独的分页搜索或
详情读取工具；引用用于回溯 run 中已有的 trial 和 evaluation 记录。

退火在 Region／方向层选择广度与深度，不用于接受或撤销单个因子。Factor Miner 的每条
候选仍消耗同一个全局 attempt，并执行相同的编译、Train/Valid 评估、准入、去重、原子记录和
恢复流程。

Miner 接收按当前计划允许字段生成的公共表达式示例；其高周期权限按基础字段校验，
因此计划允许 `adj_close` 且公共 context 允许 `@30m` 时，`$adj_close@30m` 不会被误判为
计划外字段。字段、频率、窗口和广播语义以 [DSL](../dsl.md) 为准。

Analyst 和 Miner 均可调用 `get_context`、`library_search`、`library_get`、`library_list`、
`library_stats` 五个公共只读工具。使用与 AlphaPROBE 相同的 JSON 查询批次：
`{"queries":[{"name":"library_search","arguments":{"query":"机制","source":"all"}}]}`。
每个 Analyst 计划、每个 Miner 候选最多 8 轮查询，每轮最多 16 个请求；结果通过
`query_results` 返回。Analyst 查询计入既有对话上限，Miner 成功查询不占用公式修复次数。
查询不生成候选、不扣评估预算、不扩大计划字段或算子权限；实际模型请求仍计成本。
原始回复、查询结果和 Miner 对话复用已有方法状态，恢复不重发已保存回复或重复已保存查询。

回复外包装按[公共解析规则](../method_reproduction.md#公共信息与初始化)处理。
Analyst 提示词明确计划字段类型：`hypothesis` 为非空字符串，`allowed_fields` 为当前可用基础
字段名的非空数组，`measurement_plan` 为字符串，`miner_instruction` 优先使用非空指令文本。
`miner_instruction` 也接受非空 JSON 对象，以 JSON 文本保留全部键值后交给 Miner；不挑选
其中某个键、不增补研究指令。空对象、数组、布尔值及缺失指令仍报错并返回具体字段要求。
这项兼容不改研究区域、方向、字段权限、表达式校验、准入门槛或查询／修复／候选预算，
也不改写已保存的原始回复和既有 trial。V1 和 V1 Async 共用相同处理。
修复耗尽会保留最后一条具体校验错误；Miner 的生成失败原因随原有候选 trial 保存，
可区分 JSON、字段和 DSL 失败，无需另建日志或状态账本。

### 计划阻塞与交接

Analyst 的测量路线必须能通过当前 DSL 和平台反馈执行；Miner 保持原检验，不以其他因子
替代缺失的测量。任一角色遇到缺失数据或评估能力时，只返回
`{"blocked":"具体缺失能力"}`，与正常计划／表达式回复互斥。

平台在现有方法检查点的 `blocked_plans` 中保存原计划编号、原方向和阻塞原因，结束该计划，
让 Explorer 选择其他可执行问题，或提出解决阻塞的新检验。没有表达式提交时不生成 trial，
不扣候选预算；已经提交的 trial 保留，并按真实结果更新区域累计统计，阻塞本身不触发
Metropolis 区域切换。结束数量包含阻塞计划，候选完成数量只统计实际提交。

Explorer 的 `atlas.blocked_plans` 来自这些原计划；没有 trial 的阻塞不会被误写成失败测量。
若计划已提交部分候选，完整计划正文同时给出实际 trial 与阻塞原因。连续没有新候选的
阻塞计划数受现有 `max_dialogue_rounds` 限制，达到上限明确停止；该限制恢复后仍有效。
已保存的阻塞回复复用原有阶段恢复路径，不重发、不重复结束计划。旧检查点无此字段时
视为空列表，不改历史产物。自由文本中的“BLOCKED”不代替结构化回复；自然语言检验是否
等价仍依靠角色判断与审阅，平台不声称能自动验证所有测量语义。

## 公平对照与运行

`configs/annealing.toml`、`configs/v1.toml` 与 `configs/v1_async.toml` 配置三 Agent
共用的 Chat 模型、端点和凭据环境变量名。模型及连接设置以各自配置为准。
与 MCTS-LLM 比较时，另需统一两侧模型、端点、通用模型温度、修复上限和超时设置；
两者使用同一 runner、SearchSession、数据快照、benchmark 和候选评估预算。
退火额外配置计划长度和初末温度；MCTS-LLM 的树参数仅适用于它自己的选择器。
使用相同资产、universe、fold、seed、fields 和 attempts，只切换 `--method`。

例如使用期货 fold1、seed 42 和 500 次候选评估：

```bash
uv run atlas run --asset futures --fold fold1 --method annealing --seed 42 --attempts 500
```

MCTS-LLM 对照命令只需将 `--method annealing` 换成 `--method mcts_llm`。500 次预算下，
退火版每个计划最多 5 个候选，最后一轮可以不足 5 个；模型请求次数因此不等于候选预算。
模型 key 从各方法 `chat_key_env` 指定的环境变量读取；切换模型后新建运行。

## 实现边界

AlphaAtlas 中的三 Agent 流程是对研究角色的本地适配；未复用外部 `ResearchPipeline` 的
数据、评估、准入或持久化服务。Agent 只决定方向、计划和公式；数值评估及最终准入以平台为准。
自动测试使用合成数据和模拟模型，不证明真实模型调用或市场效果。
