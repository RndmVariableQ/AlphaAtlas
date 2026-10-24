# AlphaAtlas 三 Agent 退火搜索

入口 `--method annealing`，实现标识 `annealing_llm_atlas_v1`。方法由 Atlas Explorer、
Research Analyst 和 Factor Miner 三个角色组成，退火搜索的状态是当前研究 Region。

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

Explorer 地图中的 Train/Valid 分布来自本方法已有的 run 内 trial 记录；分布包含所有评估
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
