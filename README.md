# Alpha Atlas

**在固定 train/val 区间内，把因子的语义、计算图、因子值和探索经验组织成统一地图，
帮助研究者与 AI 判断最值得探索的区域，以有限预算更快扩展有效因子库。**

目标设计与阶段状态见 [施工计划](PLAN.md)。当前已实现文本/AST DSL、
[60 个基础算子](docs/operators.md)、运行内组合/group_batch 注册、文件因子库与冻结 OOS。
使用方式与数值协议见 [DSL 与自定义算子](docs/dsl.md)。四个现有 ask/tell 基线支持检查点恢复；
另已接入 [AlphaPROBE 搜索算法适配](docs/alphaprobe.md)，支持三阶段 LLM 生成与检查点恢复。
通用 LLM 工具与任意 run(session) 控制流恢复仍属后续工作。

所有方法的接入边界与验收要求见 [方法复现与统一比较规范](docs/method_reproduction.md)。
当前状态见 [待对比方法](docs/methods_to_compare.md)；
[MCTS-LLM 算法适配](docs/mcts_llm.md) 已接入 `mcts_llm`，含五维反馈、FSA 和阶段恢复，
当前以合成数据／模拟模型验证。公共训练诊断由 `benchmark.toml` 对所有方法统一开启。

## 为什么做

生成更多表达式不等于积累更多有效信息。如果因子公式、评估结果和失败记录分散保存，
后续搜索很难判断一个方向是否已经被充分尝试，或一个新公式是否仅是已有信号的变体。

本项目统一组织不同来源的候选，保存成功、失败、重复和成本。AI 可以依据已有证据深化某个
区域，也可以提出新的机制或交互假设，登记为空的待探索区域，再用实验验证其价值。
地图空白代表证据不足，不代表一定存在 alpha。

第一版研究静态探索效率。暂不研究市场状态切换、因子间动态预测、在线交易或组合择时。
目标是提高有效非冗余因子数量与有效覆盖随预算的增长速度。

## 第一版资产与数据

统一请求区间：**2015-01-01 至 2026-09-01，端点包含**。遇非交易日不制造 Bar；
2015 年第一个实际交易日是 2015-01-05。全部时间按 Asia/Shanghai 解释，Bar 标记结束时刻。

| 资产 | 来源与频率 | 股票池／合约范围 | 其他数据 |
| --- | --- | --- | --- |
| A 股 | RiceQuant 原生日线，重新拉取 | RQ 历史 HS300 + ZZ500 + ZZ1000 的逐日并集 | RQ PIT 财务指标、全部财报版本、复权事件、ST／停牌标记 |
| 期货 | RiceQuant 原生 5 分钟，重新拉取 | 全品种逐日主力，保存对应真实合约代码 | rule=0 主力映射、合约元数据、交易日历 |

首批数据已完成获取；行数、覆盖审计和源数据例外见 [数据验收记录](docs/data_snapshot.md)。

A 股支持 `union1800`、`hs300`、`zz500`、`zz1000` 四个研究视图。1800 是逐日股票池定义，
不是在今天挑选 1800 只股票回溯历史，也不是整个历史只有 1800 个证券代码。
下载历史上曾入选的股票全部可用日线，保留入选前 Bar 作时序预热；只有当日成员参与评价。
退市股票保留，ST／停牌观测保留用于审计，在评分时排除。

### A 股价格和财务规范

- `open/high/low/close` 是未复权价格；`adj_*` 使用截至当日生效的累计复权事件构建，
  不以 2026 年末价格基准重写早期价格。价格单位人民币，成交量为股，成交额人民币。
- `configs/fundamentals.toml` 固定首批 **40 个** RQ 财务／估值衍生字段，前缀为 `funda_`。
  字段涵盖估值、盈利、增长、负债、现金流和周转率；技术指标／alpha101 不作为原始基本面导入。
- `get_factor` 的每日 PIT 观测统一延后一交易日，并保存 `fundamentals_source_day`。
  无法取得的值保留空值，禁止向前填入尚未披露的财务信息。
- `get_pit_financials_ex(..., statements='all', date='2026-09-01')` 另外保存三大表字段的
  全部历史版本、`info_date`、`if_adjusted`、`rice_create_tm`。2014 年报告仅作为早期预热证据。
  不用今天的 latest 财报覆盖过去。`rice_create_tm` 是供应商入库时间，不当作公告时间。
- 原 AlphaSeeker/Tushare 导出位于 `data/reference/alphaseeker_ashare/`，只作参考，不是活动数据源。

### 期货规范

- 调用 `get_price(..., frequency='5m', adjust_type='none')` 获取真实合约 Bar。
  不由现有 1m 重采样，不使用合成连续价格替代真实价格。
- 主力选取使用 RQ `rule=0`：以前一收盘持仓信息确定下一交易日主力；保留每日映射供审计。
- 使用供应商 `trading_date` 作为交易日，夜盘自然日期与交易日可以不同。
- 持久身份为 `(exchange, instrument_id)`；`product` 仅用于品种聚合。
  时序算子和标签都不能跨合约，主力切换后历史不足时产生空值。
- 只下载主力对应日期，暂不补充非主力时期的历史预热；这一限制对全部方法一致。
- 该项目的原生 5m 数据与 ChaoticTrader 的 canonical 1m、research/live 数据库完全分开。

来源文档：[RQ 通用行情接口](https://www.ricequant.com/doc/rqdata/python/generic-api)、
[RQ A 股与 PIT 财务接口](https://www.ricequant.com/doc/rqdata/python/stock-mod.html)、
[RQ 主力规则](https://www.ricequant.com/doc/rqdata/python/futures-mod)。

## 两组固定 fold

| Fold | Train | Val | Test |
| --- | --- | --- | --- |
| fold1 | 2016-01-01 — 2018-12-31 | 2019-01-01 — 2020-12-31 | 2021-01-01 — 2022-12-31 |
| fold2 | 2019-01-01 — 2021-12-31 | 2022-01-01 — 2023-12-31 | 2024-01-01 — 2025-12-31 |

- fold2 的训练期完整包含 2019—2021 年。
- 每个 fold 只加载训练起点前一个自然年的预热数据，fold1 为 2015 年、fold2 为 2018 年；
  预热数据不计分。平台不限制窗口和累计历史需求上限；窗口为满足算子最小要求的正整数。
  可用历史不足仍返回 null，不能跨真实合约或连续段。基线的有限候选窗口属于其自身策略。
- 2026 年数据预留，不自动加入任一 fold。
- 两个 fold 独立运行，不共享搜索结果。fold1 的 test 与 fold2 的开发期存在重叠，
  不应宣称两者是相互独立的最终盲测。正式研究先冻结全部开发决策，再批量查看 test。
- 标签的终点必须落在当前 split 内；不能把跨界未来收益留在 train/val。

## 统一对象和接口

对象定义在 `src/alpha_atlas/contracts.py`，资产差异配置在 `configs/assets/`。

| 对象 | 定义 |
| --- | --- |
| `AssetProfile` | 资产能力、频率、数据目录、股票池、价格字段、标签期限和评估口径 |
| `Continent` | 经济机制、稳定 ID、分类版本 |
| `Region` | 所属大陆、新假设、约束、提出来源；允许尚无因子 |
| `Expression` / `CompiledFactor` | AST 与规范化计算身份、实际依赖、字段、历史需求 |
| `Candidate` | 表达式与语义／区域归属，生成祖先不是必需 |
| `FactorValues` | 因子 ID、快照及上下文 ID、对齐的 row_id 与 value |
| `Metric` | 指标名称、split、聚合口径、值及样本量 |
| `EvaluationReport` | train/val 指标、训练期方向、覆盖率与耗时 |
| `TrialFeedback` | 候选、评估、入库结果、拒绝原因、最近相关程度 |

`row_id` 只在对应数据快照内解释；语义分类更新不改变表达式 ID。
跨资产可共享表达式结构、算子定义和接口，但数值、指标、地图、因子库不混在一起。

搜索方法可使用同步 session，自行创建算子和提交候选；现有 ask/tell 基线继续由 Runner 适配：

```python
def run(self, session):
    feedback = session.evaluate(Candidate("TS_RANKCORR($close,$volume,20)"))
    library = session.get_library()  # 不可变视图；不含 OOS 或标签
```

GP 内部维护种群，MCTS 内部维护树，Atlas 内部维护区域证据。算法不直接读 Parquet、
连供应商或写入生产因子库。数据仅通过 `MarketData` 读取；标签只进入评估器。
OOS 入口必须在 run 冻结后单独调用。

## 统一评估与公平对比

- 同一资产内固定数据快照、股票池、fold、字段、表达式空间、初始库、预算与规则。
- 每个 `asset × universe × fold × method × seed × run_id` 独立持有实验因子库与地图。
  不让后运行的方法读取前一方法的新发现或缓存评估结果。
- 当前四个基线共享有限文法：5 类时序变换 × 字段对 × 5 类组合 × 同一组窗口。
  AlphaPROBE 使用统一 DSL 的更广组合空间；比较时须披露空间、初始种子与模型成本差异。
- 默认标签是未来 **5 个日 Bar（A 股）／12 个 5m Bar（期货）**的收盘对数收益；
  这是初始研究协议，不是可执行交易收益。参数在资产配置固定。
- A 股主指标是逐日横截面 Spearman IC 等权平均，同时报告 Pearson IC。
  期货将同品种所有合约的评分样本合并，分别计算原始数值的 Pearson 和重新排名的 Spearman，
  各自报告品种等权和 SQRT 成交额加权两种汇总。期货主指标为加权 Pearson IC，用于训练方向、
  入库门槛与方法评分；其他三项同时报告。所有 val/test IC 沿用所属资产主指标的训练方向。
  期货指标统一命名为 `time_series_weighted_pearson_ic`（默认）、`time_series_equal_pearson_ic`、
  `time_series_weighted_spearman_ic`、`time_series_equal_spearman_ic`。
  不同资产的 IC 绝对值不能直接比较。
- 期货品种权重为 `sqrt(sum(amount))`：分别统计整个 train、val、test 区间内 eligible 行
  的有限非负成交额，权重不随因子或标签缺失改变。先合并成交额再开平方，不对 Bar 加权排名。
  品种有效样本少于 5 或 IC 不可定义时不参与汇总；零／无有效成交额的品种不参与加权汇总。
  各汇总的 `n_obs/n_groups` 只计实际参与的行／品种；无可用分母返回 null。
- 期货 60m 标签沿用未来 12 根原生 5m Bar 的 `log(close[t+12]/close[t])`，不跨真实合约或
  连续段；可跨正常休市，因此不保证墙上时钟恰好相隔 60 分钟。
- 期货入库要求 train、val 各自全区间的加权 Pearson IC，按训练集确定的同一方向调整后，
  **均严格 > 0.005**（等于也拒绝）；阈值配置为 `configs/assets/futures.toml` 的
  `min_train_val_ic`，替代期货的默认 val 门槛。A 股仍要求定向 val IC ≥ 0.01。
  两类资产均要求验证集有效覆盖率 ≥ 0.8，并在验证集与每个已入库因子的共同有效行上
  计算绝对 Spearman 相关性，要求 **< 0.7**。相关性参数为 `configs/benchmark.toml`
  的 `max_abs_corr`，后续降低该值即可收紧；最少 100 对齐行，先入库者保留，
  不足重叠视为未证实非冗余。参数随运行保存，旧运行产物不改写。
- 完整统计尝试数、真实评估数、重复／非法／低质量比例、生成耗时、评估与查重耗时、
  总运行耗时和因子积累曲线。AlphaPROBE 记录模型请求及供应商返回的 token 用量；
  默认预算仍按尝试数，尚未实现 token 计价或硬总成本预算。
- test 不参与搜索、符号选择、入库或重新选因子。`frozen/library.json` 保存定义与依赖，
  OOS 核对配置、数据和冻结哈希后才能读取已有报告；源码指纹仅记录来源，不阻止续跑或评估。
- val 持续反馈给搜索算法，因此它是开发验证集，不是最终样本外证据。

主报告：有效非冗余因子积累曲线、单位有效增量成本、冻结因子库的 OOS IC 分布。

可选基础定义库：[Futures CTA 统一 DSL 适配](src/alpha_atlas/factor_libraries/futures_cta/README.md)。
独立目录提供 22 个公式，通过 `load_library("futures_cta", frequency="5m", bars_per_day=48)`
显式加载；48 仅是示例的名义日换算尺度。支持缺失字段查询与普通 Candidate 输出，
尚未接入 Runner 的初始入库、预算和增量报表；默认搜索仍从空库开始。

可选远月输入支持 `_p1/_p2` 后缀的 OHLC、成交量、成交额、持仓量与到期天数。
严格按同一交易日和 5m 结束时点对齐，按表达式实际依赖的合约腿隔离窗口；
扩展导出使用独立 `futures_curve` 数据集。接口与显式采集命令见
[远月合约字段](docs/dsl.md#远月合约字段)。`--merge` 可将本次下载分区合并成
`data/futures_curve/bars_5m.parq`；`--merge-only` 只合并已有分区。实际覆盖与缺失见该目录
`manifest.json`，合并不会将不完整采集改为完整。
2026-09-10 已实际采集并合并 12,113,015 行；2 项远月同刻报价缺失原样留空，
详细范围与复查结果见[远月扩展采集记录](docs/data_snapshot.md#2026-09-10-远月扩展实际采集)。

原生 5m 还支持 `$close@15m`、`$close@30m`、`$close_p1@60m`、`$volume_p2@1d`。
所有方法共用 `library_search` 检索本次入库成员或可选 CTA 基础定义；
`configs/benchmark.toml` 的 `reference_library` 设为空可统一关闭基础库访问。基础公式不预先入库，
仍由平台按固定绩效、覆盖率及相关性门槛自动准入，并作为 evaluate 结果返回给 agent。
同周期子树先在粗周期计算，再按已知发布时间广播；混合周期在 5m 上组合。
日线按交易日汇总，在下一交易日首根 Bar 确认后发布，避免根据盘中截断的最后一行判断收盘。
聚合与边界详情见[高周期规则](docs/dsl.md#高周期特征与无前视广播)。

有效覆盖需采用预先确定、对所有方法一致的分类评价；AI 动态提出的区域不能直接算作成功。
固定组合的成本后收益／Sharpe、相同 K 对比、有效秩等属于后续评估模块，当前不伪造这些指标。

## 实现范围

已提供：统一对象、资产配置、两组 fold、原生数据获取与断点续传、文本/AST DSL、60 个基础
算子、运行内组合与受限 group_batch 创建、train/val 评估、run 隔离、文件证据、只读因子库、
冻结 OOS IC 和基础对比报告。代码算子经本地子进程统一验证后使用 Numba 执行，golden 使用 NumPy；基础算子继续走 Polars。

搜索器是可运行的最小基线：Random、固定类型文法 GP、UCT MCTS、区域 UCB 导航。
**当前 MCTS 不等于《Navigating the Alpha Jungle》的 LLM-MCTS 复现；当前 Atlas 不含
LLM、语义 embedding、自动开放式假设生成或学习式多模态地图。** 这些能力通过同一协议后续接入。
现有 GP 的空间是固定模板／类型文法，不是任意深度程序树 GP；正式论文比较需要扩展并调参。

另有独立的 `alphaprobe` 方法：保留演化 DAG、叶／非叶贝叶斯检索、三种多样性与祖先路径
驱动的 Analyst/Execution/Validator 生成。数据、DSL、算子、正式准入和冻结 OOS 均使用本项目。
质量采用绝对 train IC，详细公式、适配差异和模型配置见 [AlphaPROBE](docs/alphaprobe.md)。
默认无预置种子，由 LLM 生成首批根；有种子实验使用 `initial_expressions` 显式配置。
根免子代质量门槛，正式入库规则不变。这是搜索算法适配，不是原论文收益复现；旧版模板
初始化已有真实运行产物，新冷启动版本尚待市场实验。

## 目录

数据、已完成实验与待开展计划见[实验总览](docs/experiment_overview.md)。

```text
configs/                 资产、财务字段、fold、实验规则
src/alpha_atlas/
  contracts.py           公共对象与 Protocol
  assets/                RQ 获取、.parq 导出、MarketData、数据审计
  expressions.py         文本/AST 编译与执行
  operators/             基础算子、注册、受限代码、统一验证与 Numba 运行时
  evaluation.py          标签、split、安全评估
  library.py             统一因子库准入
  atlas.py               区域与探索证据
  methods/               ask/tell 搜索基线与 AlphaPROBE 搜索适配
  session.py             同步候选/算子接口、尝试预算、自动准入
  runner.py              统一实验循环与冻结
  storage.py             原子 trial JSON、只读库重建
  reporting.py           积累曲线与对比 JSON
data/                    数据文件，独立于代码，不进入 Git
  ashare/bars/year=*/    原生日线 + 延迟后的 PIT 财务字段 .parq
  ashare/fundamentals/   原始财报版本与每日指标 .parq
  ashare/metadata/       历史成员、日历、复权事件、字段目录 .parq / .json
  futures/bars/year=*/   主力真实合约原生 5m .parq
  futures/metadata/      主力映射、合约、日历 .parq
artifacts/runs/          独立实验结果，不进入 Git
tests/                   纯合成数据测试，不访问真实凭据或供应商
```

每个运行目录保存 `run.json`、`operators/`、`trials/`、`cache/`、`frozen/`。
统一阅读入口为根目录的 `report.md`，独立 OOS 原始结果为同层 `oos.json`。
新运行不再生成 `reports/atlas.json`；区域统计直接从 trial 汇入报告，旧产物保留。
可恢复方法另存 `checkpoint.json`，运行异常追加到 `failures.json`；不增加数据库或调度服务。
JSON 为研究证据，Arrow IPC 为可重建缓存；只有 MarketData 的数据读取边界仍使用 DuckDB。
`run` / `resume` / `test` 默认向 stderr 实时打印紧凑进度行；每个候选完成后只有一个外框，
指标按行列对齐显示原始训练 IC 和定向验证 IC，星号标注主指标。包括加载、公式、覆盖率、
入库结果、耗时及 OOS；终端需至少 79 列。长内容按显示宽度换行，不清屏或覆盖历史输出。
`run` / `resume` 的 stdout 仍输出 JSON，`$result = ... | ConvertFrom-Json` 仍可用。
`uv run atlas test $result.run_dir` 默认按加权方式分别打印 OOS 表格（每张 88 列），期货先显示
SQRT 成交额加权，再显示品种等权，两表因子顺序一致。列为因子 ID 前 12 位、状态、
有效样本、组数、覆盖率，最右两列为 Spearman IC、Pearson IC。
各相关指标分别排除不可定义的组；样本/组数不同则按 Spearman/Pearson 显示两个数字。
空库、未定义指标及计算错误明确显示。旧 JSON 未保存 Pearson 时显示 —，只读报告不补算。
落盘仍为 `oos.json`。需在管道中解析 OOS 时使用 `atlas test <run目录> --json`。
已经计算过的 OOS 可用 `uv run atlas report $result.run_dir --oos` 直接显示表格。
这是只读展示已保存结果，不加载行情、不写文件，也不要求当前代码与旧运行一致。
`test` 仍校验当前代码、配置与数据；显示代码更新后，查看旧结果使用 `report --oos`，
无需重跑搜索。尚无 oos.json 时，该命令明确提示先计算 OOS。
加 `--quiet` 只关闭进度，仍输出最终表格或 JSON。Python 接口默认安静；需要时启用
`alpha_atlas.progress` 的 INFO 日志。
Runner 的评估和成员磁盘缓存共用 512 MiB 上限；恢复时统计已有缓存，超限清理数值文件，
保留全部定义和 trial。成员值在查重需要时重建，单个超限结果不留磁盘缓存。
旧实验格式不会静默迁移，比较报告会列出不支持的旧运行。

### 统一运行报告

`report.md` 汇总配置与状态、已提交尝试和剩余预算、成员公式及 train/val 指标、入库进度、
拒绝原因、区域统计、算子注册、中断失败和已存在的 OOS 结果。运行结束、中断和 OOS 后自动
刷新；也可以独立重建：

```powershell
uv run atlas report artifacts/runs/<run_id>
```

报告只读取已有 JSON，不读取行情、重算因子或触发 OOS。预算拒绝计入日志条数，不扣候选
尝试预算；未知耗时明确标注，累计时间字段不重复相加。报告写入失败会提示重建命令，
不撤销已提交结果或掩盖原运行异常。`comparison.json` 继续用于跨运行机器读取。

### 方法的只读上下文

```python
context = session.query("get_context", {})
print(context["asset"], context["frequency"], context["target"], context["metric"])
print(context["fields"], context["operators"])
print(context["expression_rules"], context["evaluation_rules"])
print(session.query("library_search", {"source": "reference", "limit": 5}))
```

所有方法使用同一套公共研究工具。context 一次返回资产、频率、目标、指标、字段、算子、
表达式／评估规则和参考库设置；具体日期、fold、运行/快照 ID、资产池标识留在平台内部。
实时 remaining_attempts 只放在评估结果中，不进入模型 context 或固定提示词；平台内部
仍维护预算和停止条件。字段与规则不会因评估扣减预算而改变。
算子详情包含签名、默认值、作用域、输出、历史需求、最小窗口、版本及别名。
目录包含 60 个基础算子和本运行成功注册的组合/group_batch；查询返回独立副本，旧结果不变。
普通 ask/tell 方法通过 `set_session(session)` 获取同一查询入口；AlphaProbe 和 ReAct 的
模型查询复用该实现。候选均由 runner 调用 session.evaluate，保留统一预算和原子准入。
完整输入输出见[公共研究工具示例](docs/react_tools.md)。

### 单 agent ReAct 基线

```powershell
uv run atlas run --asset futures --fold fold1 --method react --attempts 100
```

`react` 使用 AgentScope 的单 agent ReAct 循环，不需要 embedding。系统提示词一次性提供目标、
允许字段、完整算子目录及研究规则；模型通过标准 tools API 在一轮中可发出多个工具调用，
再由平台逐个评估因子或执行只读库查询。
目标是在预算内积累高质量且多样的因子，不预置种子，也不开放算子创建工具。

AgentScope 按 context 比例自动调用同一模型压缩旧上下文，保留最近消息；压缩和查询不扣评估预算。
状态与模型用量复用 `checkpoint.json`，支持 `atlas resume`。控制台流式显示模型思考和回复，
分段显示评估表达式、对齐的指标及入库因子名；`--quiet` 隐藏这些输出，stdout 保留 JSON。
配置在 `configs/react.toml`，协议、提示词查看方式和失败行为见 [ReAct 方法说明](docs/react.md)。

### 中断与恢复

新运行的 random/gp/mcts/atlas/alphaprobe/react 在生成候选后、消费反馈后保存 JSON 检查点。Ctrl+C 后
记录 `interrupted`；普通运行异常记录 `failed` 及发生阶段、trial 编号和异常位置。
候选编译失败或未准入仍是正常 trial，不等于运行失败。恢复沿用原 run_id 和原预算：

```powershell
uv run atlas resume artifacts/runs/<run_id>
```

已提交 trial 复用原反馈，不重复评估、入库或扣预算；尚未提交的计算重做。恢复核对配置、
代码/锁文件、方法类型、数据快照、原有算子身份和提交位置。成员缓存缺失或 IPC 无法读取时按需重建，
不会重新准入；只加载 train/val 数据。冻结途中失败则继续完成冻结，不追加搜索。
检查点、算子记录集合和成员缓存不增加额外哈希；可正常读取的缓存直接复用，不检测静默数值修改。
同一运行使用操作系统文件锁限制单个写者，进程退出即释放；无需手工删除 `.run.lock`。

`run.json` 复用现有状态字段：running/interrupted/failed/frozen，固定配置仍受指纹保护。
累计耗时包含已记录活动时间和恢复重算，排除两次运行之间的等待；强制结束时尚未记录的
尾部耗时标记为未知。磁盘故障可能使失败记录也无法写入，此时保留原异常并附加说明。

自定义 ask/tell 方法实现 `dump_state() -> dict`、`load_state(state)`，保存全部 RNG、参数和
待反馈状态；仅允许 JSON 值。恢复调用 `resume(root, run_dir, method_impl=原方法实例)`，
实例状态由检查点覆盖。tell 不应产生外部副作用，可能从旧状态重新消费已提交反馈。
AlphaPROBE 的 ask 另在模型调用前后通过 `set_checkpoint` 回调保存阶段状态；已落盘响应
不重复请求，未落盘调用的费用保持未知。其批量子代逐个沿用同一 trial 恢复协议。
任意 `run(session)`、初始化尚未生成检查点的失败及旧无检查点运行暂不支持恢复。
本地 Numba 内核执行中不承诺立即响应 Ctrl+C；不保存 Python 调用栈，也不加载 pickle。

`manifest.json` 记录请求范围、实际覆盖、分区哈希、行数、缺口和状态。未完成的数据集默认
不能用于实验；`--allow-incomplete` 仅用于明确标记的诊断，不能作为正式基准结果。
行情／财务空值保留，不把空值填成零。`audit.json` 保存结构完整性检查，不能替代全面金融质量审计。
供应商原始 OHLC 异常保留在文件中；`MarketData` 将异常行的数值输入置空并排除评分，
仍保留行位置，避免滚动窗口悄悄跳过异常。审计分别报告原始完整性与执行隔离策略后的可用性。
指数成员有时在股票退市后才移除。缺行情的成员日保存在 `metadata/coverage_exceptions.parq`，
按 RQ 上市／退市日期区分存续期外记录与未解决缺口；前者不补造价格，后者阻止获取完成。

## 本地使用

```powershell
cd D:\WORKSPACE\AlphaAtlas
uv sync --extra ricequant
uv run atlas config
uv run atlas data-status
```

重新获取／续传数据（凭据从环境变量或指定现有文件读取，不复制进项目）：

```powershell
uv run python scripts/acquire_data.py futures --env-file D:\WORKSPACE\ChaoticTrader\.env
uv run python scripts/acquire_data.py ashare --env-file D:\WORKSPACE\ChaoticTrader\.env
uv run atlas audit-data --asset futures
uv run atlas audit-data --asset ashare
```

数据客户端使用最多两个共享连接，避免工作线程数量增加触发账号连接数限制。

配置、请求日期或字段更改后应使用新数据快照目录；同一目录的断点续传用于相同请求。

### 本地 Chat 与 Embedding 服务

AlphaProbe／MCTS-LLM 当前使用 Clovapi 提供的本地 OpenAI 兼容 Chat 代理。代理配置保存在
Clovapi 自己的 profiles 中，AlphaAtlas 不保存 API key：

```powershell
clovapi proxy start
clovapi proxy status
```

当前 AlphaAtlas 配置使用 `gpt-5.5` 和 `http://127.0.0.1:27483/codex/v1`。模型 profile
需要先在 Clovapi 中完成 Codex OAuth 或其他供应商配置；不要把凭据写进本仓库。

Embedding 使用本地 `Qwen3-Embedding-0.6B`。模型目录为
`D:\WORKSPACE\models\Qwen3-Embedding-0.6B`，服务端口为 `8003`。已有容器可这样启动：

```powershell
docker start alphaatlas-qwen3-embedding-0p6b
```

首次部署或容器不存在时：

```powershell
docker run -d --name alphaatlas-qwen3-embedding-0p6b `
  --restart unless-stopped `
  -p 127.0.0.1:8003:8000 `
  -e EMBEDDING_MODEL_DIR=/models/Qwen3-Embedding-0.6B `
  -e EMBEDDING_MODEL_NAME=Qwen3-Embedding-0.6B `
  -e EMBEDDING_THREADS=8 `
  -v D:\WORKSPACE\models\Qwen3-Embedding-0.6B:/models/Qwen3-Embedding-0.6B:ro `
  -v D:\WORKSPACE\AlphaAtlas\tmp\qwen3_embedding_server.py:/server.py:ro `
  --entrypoint python3 `
  vllm/vllm-openai:v0.29.0-x86_64-cu129 /server.py
```

验证两个服务：

```powershell
Invoke-RestMethod http://127.0.0.1:27483/codex/v1/models
Invoke-RestMethod http://127.0.0.1:8003/health
```

运行探索实验：

```powershell
uv run atlas run --asset futures --fold fold1 --method random --seed 42 --attempts 10
uv run atlas run --asset futures --fold fold1 --method mcts --seed 42 --attempts 10
uv run atlas run --asset ashare --universe hs300 --fold fold2 --method gp --attempts 10
uv run atlas run --asset ashare --fold fold1 --method atlas --attempts 10 `
  --fields adj_close,volume,funda_return_on_equity_ttm,funda_debt_to_asset_ratio_lf
uv run atlas compare
```

上述不同字段、资产或股票池的命令用于展示入口，不构成公平的互相对比。正式对比需统一条件。
`atlas run` 返回唯一 run 目录；冻结后执行 `uv run atlas test <run目录>`。
OOS 仅输出冻结全库的 IC 审计，当前没有交易回测或发送订单接口。

冻结因子库还可用于公共 `linear`／`lightgbm` 预测模型评估：

```powershell
uv run atlas model artifacts/runs/<run_id>
uv run atlas model artifacts/runs/<run_id> --test
```

首条命令只训练并报告 train/val；第二条使用保存模型计算 test，不重拟合。
结果在该运行的 `model/`，不会改写单因子结果。旧库作为独立模型实验导入，
数据、预处理、模型冻结和来源检查见 [model 说明](docs/model.md)。

## 开发与测试

```powershell
pwsh -File scripts/check.ps1
uv run python examples/complex_operator.py  # 完整 Numba 内核及 TS→CS 示例
uv run python scripts/benchmark_operators.py
```

Python 3.13、Polars、100 字符行宽；版本锁定于 `uv.lock`。
自动化测试覆盖合约隔离、标签边界、表达式校验、因子库隔离和 OOS 冻结，永不拉取真实数据。
普通检查包含真实 Numba 编译、子进程注册门槛、本地执行及冻结 OOS 集成测试，无需 Docker。
`data/`、`artifacts/`、`.env`、凭据和缓存均不入 Git。
