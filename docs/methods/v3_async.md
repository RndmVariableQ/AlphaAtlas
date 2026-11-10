# V3 Async：入库残差奖励与 Region 地图

方法名为 `v3_async`，单方法配置可自动选择；实现标识 `atlas_mechanism_v3_async`，检查点版本独立。
流程沿用 [V2 Async](v2_sync.md#v2-async)：V1 Async 的并发计划、命名机制 Region 和
Metropolis 退火。下列改动各对应一项已测得的 V2 问题；比较与原因见
[实验记录](../records/experiments.md)。

主源码的 V3 与并发流程来自本地 `feature/prompt-cleanup` 的 `ce1ad8c`，残差准入来自
v10 运行时的平台改动；接入时合并了主线已有的模型、评估与观测台修改。

| 改动 | V2 的问题 |
| --- | --- |
| Region 得分 = 入库因子 `residual_ic` 之和 ÷ 尝试数 ÷ `sea_level`；未入库尝试计 0 | 每次尝试的平均 Val 残差由噪声主导，正负各半，与 Test 不相关 |
| 地图状态以平台准入判定 | “最佳单次残差 ≥ sea_level” 随尝试数从误判海床转为误判陆地 |
| （分档运行，已由下方取消分档替代）Region 状态由已测分档推导；活动 Region 仍有开放的已测档时继续深挖 | Region 层判海床时分档仍未测完，调度与提示词矛盾 |
| （分档运行）分档改为日内／1–20 日／20 日以上，每档试满 20 次才判 seabed | 1 小时界线不区分信息与 Test；10 次即关闭使成交量、持仓量方向过早放弃 |
| Explorer 可提交一至四个方向 | 只剩一个可执行方向时被迫报告 unresolved 而提前停机 |
| Explorer 不能报告 unresolved：提示词写明当前方向受阻（区域 busy／full／关闭，或缺数据、工具）时搜索新方向——换一个可用 Region 或提出新机制；若仍回复 unresolved，平台在对话内退回并给出同一要求 | 三组 Haiku 在 1000 次预算中分别于 437、515、912 次因 unresolved 停止；理由包括候选区域暂时 busy、可用区域尚未筛完，而停止时 69 个区域中入库最多的仅 3 个 |
| 提示词写明基础频率下每小时、每交易日的 bar 数 | 模型把 5m 的一个交易日误算为 288 根 bar |
| 取消回看跨度分档（`scale_bands = "none"`）：Region 按全部尝试判定状态，提示词不再列分档；深挖在活动 Region 仍为 probing／land 时继续 | 三档各有试探预算，且“未测档算新方向”，把预算推向长回看：V3 Luna 无深挖／并行组 16%–29% 的尝试回看超过 20 日（V1 Luna 3%）；fold2 全部 V1／V3 运行中，回看超过 5 日的尝试占 43%，入库率 7.0%，入库因子 Test IC 0.0039、Test/Val 0.43，回看不到 1 日的为 3.9%、0.0059、0.82 |
| Region 选择规则在提示词中只出现一次：breadth 偏好、depth 规则和可用性各一段；删除继承自 V1 的“覆盖增多后回访”“breadth 模式比较其他 Region”两句 | 同一提示词中“回访有希望方向”“优先入库少的 Region”“只在有未测问题时回访”三处说法方向不一致 |

## 地图状态

每个 Region 用全部尝试判定状态，不按回看跨度拆分：

| 状态 | 条件 |
| --- | --- |
| probing | 尝试少于 `probe_attempts` |
| land | 有入库且 `residual_ic > 0` 的因子 |
| seabed | 尝试达到 `probe_attempts`，没有上述因子 |
| saturated | land，且尝试达到 2×`probe_attempts` 后最近 `probe_attempts` 次没有上述因子 |

状态由原子 trial 每次推导，方法与观测台共用 `region_status`，不另存状态。入库与否只由平台准入裁决；
单次 `residual_ic` 仍作为评估反馈展示，但不决定地图状态或得分。深挖沿用 V2 的 `adaptive_depth`：
活动 Region 关闭时下一轮转为 breadth，仍为 probing 或 land 时保持 depth。只改时间框架仍不算新方向。

### 分档运行（`scale_bands = "intraday_1d_20d"`）

部分 V3 运行（包括 v10 残差准入实验）按回看跨度（`feature_span_minutes`，整个表达式从最旧到最新读取的 bar，
一个交易日按 240 分钟）分为 `intraday`（≤1 个交易日）、`1d_to_20d`、`over_20d` 三档，每档按上表
判定；只由已测档推导 Region：任一已测档为 land 即为 land，已测各档都为 seabed／saturated 时关闭，
其余为 probing；未测档照常列出但不阻止关闭。这些运行恢复时沿用记录的分档和原提示词（`BAND_PROMPT`），
观测台照常展示分档表。

## Region 可用性

可选参数 `region_admission_limit`（每个 Region 最多入库因子数）与 `region_plan_limit`（每个
Region 同时运行的计划数）。未设置时不生效，旧运行配置不变；设置后写入配置。

问题：2026-11-05 的 V3 运行中，单个 Region 占全部入库的 53%–66%（DeepSeek low 串行组 66%、
Luna 16 路 62%），后半程新增几乎全部来自这一个 Region，残差 IC 逐步下降。原因有三点相互叠加：
退火进入 depth 后只围绕活动 Region 提方向；持续入库使该 Region 一直保持 land，不会关闭；
多个并发计划和多个 Explorer 同时深挖同一 Region。

做法：由平台给出唯一的可用性判断 `_unavailable(region_id)`。Region 入库数达到
`region_admission_limit` 时为 full，运行中的计划数达到 `region_plan_limit` 时为 busy，其余为可用。
以下四处都读这同一个判断，不另设规则：

| 位置 | 行为 |
| --- | --- |
| 地图 | 每个机制 Region 带 `available`；不可用时附 `closed_reason`。提示词说明含义，Explorer 直接据此选择 |
| 提案校验 | 指向不可用 Region 的提案退回；同一批次的方向须属于不同 Region |
| 深挖 | 活动 Region 缺失、不可用，或已有 Explorer 以 depth 占用时，本轮转为 breadth，并记录 `mode_reason` |
| 退火 | 计划结束时，若活动 Region 已关闭（seabed／saturated）或 full，就释放活动位，由刚结束计划的 Region 接任，避免旧的累计得分锁住退火 |

不深挖的运行沿用 V1 的 `selection_policy`（如 `greedy`）：每轮都是 breadth，Explorer 输入中不含
mode 和活动 Region，退火不再切换活动 Region，提示词中的 depth 规则换成只做 breadth 的说明
（与 depth 版共用同一句 breadth 偏好）。
方向排序与默认相同，都按评审得分取前几个。动机是旧运行的后半程（depth）中，单个 Region 占
22%–76% 的尝试；11 组中有 8 组后半程入库因子的 Test IC 低于前半程。这一对比混有库变大的影响，
因此用可用性组与无深挖组对照验证。

full 不随时间解除；busy 在计划结束后自动解除。原有地图状态（probing／land／seabed／saturated）和
得分保持不变。地图在 Explorer 对话开始时生成；对话期间若可用性发生变化，由提案校验退回并说明原因。

## 配置

方法参数为 V1 Async 参数加 `sea_level`（默认 0.005，只作得分单位）与 `probe_attempts`
（默认 20，按 Region 计；分档运行按档计）。
`scale_bands` 默认 `none` 并写入配置；`intraday_1d_20d` 只用于复现和恢复分档运行。2026-11-05 完成的
四组运行使用 10 次、V2 分档和三档全关规则；配置中没有 `scale_bands` 的运行由观测台按该旧规则
（`legacy_1h_1d`）展示。`scale_statistics`、`mechanism_regions`、
`adaptive_depth` 固定开启，不接受 `region_budget_share`；改变这些设置会报错，而不是另成一个
未记录的变体。版本须开启 `library_residual = "residual_ic_v1"`。V1、V1 Async 和 V2 的提案下限
仍为两个。

## 默认实验入口与残差准入

通过统一入口执行：

```bash
uv run atlas run --config rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_residual_v10 \
  --seed 42 --attempts 1000 --search-only
```

该版本保留实验的 `intraday_1d_20d` 分档、8 个并行计划和评估、3 个并行 Explorer。
`--search-only` 只搜索并冻结；也可通过 `--config` 选择保存的 `_r2`、`_r3` 配置。

该版本由平台设置 `library_residual = "residual_ic_v1"` 和 `min_residual_ic = 0`：原有准入
通过后，只有已测得且严格大于零的 Val 库残差 IC 才能入库，否则原子反馈为
`residual_threshold`。其他未设置阈值的版本保持原准入规则；该规则是查看历史 Test 后提出的
探索性设置，不代表独立盲测结论。

版本固定数据快照、字段、算子与模型设置；数据和历史运行产物不随 Git 分发，在线 LLM
及并发完成顺序可能变化，因此重新搜索不保证逐次产生相同候选或结果。复算已冻结的历史结果
需要对应运行产物、数据快照及依赖，不再请求 LLM；重新搜索须提供版本配置中兼容的模型服务
（`http://127.0.0.1:27483/codex/v1`）及 `ATLAS_EXPLORER_OPENAI_API_KEY` 环境变量；修改已冻结
版本的配置须另存新版本。
复核已保存模型的 Test 指标时，可复制运行目录，保留 `run.json`、`frozen/` 和 `model/fit.json`，
在副本中移除 `model/test.json` 后执行 `uv run atlas model <副本目录> --test`；原运行保持不变。
