# 数据验收记录

历史采集证据；通用约定见[实验规范](../research_protocol.md)，流程验收见[实验记录](experiments.md)。

## 2026-10-23 Research kernel 完整字段导出

按用户要求补齐 Kernel 中已有的 `adj_open`、`adj_high`、`adj_low` 和 `open_interest`，
新增独立资产 [futures_kernel_5m_full](../../configs/assets/futures_kernel_5m_full.toml)，
数据保存在 `data/futures_kernel_5m_full/`。默认输入为快照中全部八个数值特征：

```text
adj_open, adj_high, adj_low, adj_close, adj_vwap, volume, amount, open_interest
```

通过 Kernel 的只读 `/api/dataset`、`/api/query` 核对源 schema 并导出，
来源仍为 `market.bars_1m` 的 RiceQuant final 主力真实合约；没有直接打开源数据库或重新调用供应商。
保留按 `received_at`、`ingest_seq` 取最新观测、连续五根 1m 分桶、缺口重置、
真实合约与交易日隔离、夜盘归属、尾部不足五根保留及末根时间标签的原有口径。

| 字段 | 五分钟聚合 |
| --- | --- |
| adj_open | 第一根的值，保留首值缺失 |
| adj_high / adj_low | 全部源值有限时取最大 / 最小，否则为 null |
| adj_close | 最后一根的值，保留末值缺失 |
| adj_vwap | 对有限价格和正成交量做量加权；无正权重时为 null |
| volume / amount | 分别汇总 volume / turnover |
| open_interest | 最后一根持仓量，保留末值缺失；不求和、不做价格复权 |

实际导出覆盖 2012-01-04 至 2026-09-28，177 个月、97 个品种、4,758 个真实合约，
原始来源 67,795,608 行，输出 13,559,126 行。全量复权 OHLC、volume、amount 和 OI
均无空值或非有限值；`adj_vwap` 的 1,381,618 个源口径缺失按原样保留，没有填补。
MarketData 快照为：
`73db06c2d7223ce5829b8d3d9846a27b35e59e0d621a11a4a94d99dcc6ec0e87`。

标准 `atlas audit-data` 检查通过，`passed=true`、`research_ready=true`；
分区哈希不一致、重复 row_id、空主键、异常 OHLC 和缺失 OHLC 均为 0。
MarketData 与审计在没有完整原始 OHLC 时，按复权 OHLC 执行同样的有限性、价格区间和异常隔离规则。
在 2016-01-04 的实际训练期观测上验证了八字段读取，以及复权价格和 `open_interest@60m` 的编译执行。

```bash
uv run python -m alpha_atlas.assets.research_kernel \
  --url http://127.0.0.1:8791 --output data/futures_kernel_5m_full
uv run atlas audit-data --asset futures_kernel_5m_full
uv run --env-file .env atlas run --asset futures_kernel_5m_full --universe all \
  --fold fold1 --method mcts_llm --seed 42 --attempts 1000
```

新运行省略 `--fields` 即使用全部八字段。原 `data/futures_kernel_5m/` 与其资产配置保留，
既有 AlphaPROBE 的配置和四字段 MarketData 快照核对仍有效；旧运行继续沿用冻结字段，
完整字段实验使用新资产和新运行。导出器拒绝将完整字段任务写入已有四字段目录，防止混用。
合成测试覆盖最新记录选择、OHLC 手算、OI 末值与空值、非有限价格、缺口、合约隔离、
夜盘、异常隔离、默认字段和按月恢复。
实际数据、分区校验与缺失计数见该目录 `manifest.json`、`audit.json`；
过程日志见 `artifacts/monitors/kernel_full_20261023.log` 和对应 `_audit.log`。

## 2026-10-19 Research kernel 复权五分钟导出

用户明确要求从本机 ChaoticTrader_Future research kernel 保存 `adj_close`、`adj_vwap`，
并将原生 1m 合并为 5m。来源为 kernel 的 `market.bars_1m`：RiceQuant、final、
所有品种的逐日主力真实合约。导出前只读核对覆盖 2012-01-04 至 2026-09-28，
原始主力记录 67,795,608 行；同一观测按 received_at、ingest_seq 保留最新版本。

本节为当时的四字段版本；当前完整字段导出见上方 2026-10-23 记录，使用独立目录与资产。

[导出器](../../src/alpha_atlas/assets/research_kernel.py) 只调用 kernel 的 `/api/dataset`
和只读 `/api/query`，不直接打开源数据库。输出为独立的 `data/futures_kernel_5m/`，
逐月 `.parq`、状态、实际覆盖、行数、空值与源质量计数以该目录 `manifest.json` 为准。
不导出标签，不覆盖既有 `data/futures/` 原生 5m 或历史运行。

```bash
uv run python -m alpha_atlas.assets.research_kernel \
  --url http://127.0.0.1:8791 --output data/futures_kernel_5m
```

独立资产配置为 `configs/assets/futures_kernel_5m.toml`，以 `adj_close` 为目标价格，
沿用期货 product panel、12 根 5m 目标和 weighted Pearson IC。默认开放全部四个导出特征字段：

```bash
uv run atlas run --asset futures_kernel_5m --fold fold1 --method v1_async --seed 42
```

相同参数可将 `--method` 换为 `alphaprobe`；现有原生期货配置仍指向 `data/futures/`。

聚合沿用 kernel 的 session-aware MarketData 口径：每个交易所、品种、真实合约和交易日
分别按连续五根 1m 分组；间隔不等于一分钟时重置，保留尾部不足五根的组，输出时间为
组内最后一根已观测分钟的时间。`source_bars` 保留每组实际分钟数；这不是原生 5m 数据。

- `adj_close` 取末值，末值缺失时仍缺失。
- `adj_vwap` 为 `sum(adj_vwap * volume) / sum(volume)`，仅使用有限价格及正成交量；
  分母为零时为 null。保留 kernel 已有的 clamped、proxy 和 no_trade 处理及其分钟计数。
- `volume` 求和，kernel 的 `turnover` 求和后命名为 `amount`；保留复权因子及自然观测键。

复权价格直接使用 kernel 的换月累计比例结果，不在本项目重新调整或补造缺失。
这是用户另行指定的派生数据快照，不能冒充原生 5m、不复权数据的同口径对照。
合成数据验证覆盖量加权手算、零成交量、去重、合约隔离、夜盘归属、缺口重置及断点继续。

## 2026-09-10 远月扩展实际采集

用户授权后通过 RiceQuant 实际下载原生 5m，主力加历史已上市的前两个更远到期合约，
独立保存在 `data/futures_curve/`。请求区间 2015-01-01 至 2026-09-01，实际交易日覆盖
2015-01-05 至 2026-09-01；90 个品种、141 个月度分区、12,113,015 行、35 列。

单文件为 `data/futures_curve/bars_5m.parq`，565,735,865 字节（约 539.5 MiB），
按 timestamp、exchange、instrument_id 排序。合并校验行数及观测键唯一性通过，
分区保留用于断点续传，文件信息记在原 manifest 的 `merged_file`。

| 覆盖项 | p1 | p2 |
| --- | ---: | ---: |
| 已选定合约的主力行 | 12,080,089 | 11,900,041 |
| 有同刻有限 close 的行 | 12,080,088 | 11,900,040 |
| 当时不存在该远月腿的行 | 32,926 | 212,974 |
| 已选定但无同刻报价的行 | 1 | 1 |

下载请求失败为 0，主力映射日缺口为 0。唯一缺失时间点是 CZCE TC1601 的
2016-01-08 09:00（Asia/Shanghai），p1=TC1602、p2=TC1603 均无同刻报价。
对三个合约按原生 5m 单独重新查询确认：TC1601 当日 76 行，两个远月各 75 行，
09:00 只有主力记录。保留两个空值，manifest 如实标记 `incomplete`，不补齐或使用未来报价。
这不等同于所有 OHLC 均通过研究质量检查，MarketData 仍执行原有异常隔离。

与原 `data/futures` 按交易所、真实合约、时间逐行核对，原始行情字段及身份完全一致；
新导出的内部 `row_id` 重新生成，不能据此跨数据集关联，须使用上述自然键。
以上为当时的采集与复查结果，不代表当前机器的数据状态；当前状态需重新读取 manifest 与 audit。

## 首批范围

验收日期：2026-09-09。请求区间：2015-01-01 至 2026-09-01，含端点。
两类资产实际交易日覆盖均为 2015-01-05 至 2026-09-01，各有 141 个月度分区。

| 项目 | A 股 | 期货 |
| --- | --- | --- |
| 来源 | RQ 原生日线与基本面，重新获取 | RQ 原生五分钟，重新获取 |
| 行情行数 | 8,311,597 | 12,113,015 |
| 历史证券／合约数 | 3,479 | 4,257 |
| 研究范围 | 历史 HS300 + ZZ500 + ZZ1000 逐日并集 | 90 个品种的逐日主力真实合约 |
| 分区哈希不一致 | 0 | 0 |
| 重复 row_id／空主键 | 0 / 0 | 0 / 0 |
| 未解决的应有行情日缺口 | 0 | 0 |
| 原始 OHLC 异常 | 0 | 3，保留原始记录并在研究接口隔离 |

A 股行情包含历史成员入选前、退出后的可用日线，供滚动预热和审计。
当日成员观测共 5,102,977 行；仅符合成员、ST／停牌和行情质量规则的行参与评分。
两个活动数据目录合计约 2.27 GiB，独立存放于 `data/ashare` 与 `data/futures`。

## 基本面

- 已取得 40 个每日财务／估值字段，行情表中统一加 `funda_` 前缀。
- 财务来源日统一延后一交易日使用；可用日期违规和缺失 ST／停牌标记均为 0。
- 另存 466,228 条财报版本记录，包含 17 个三大表字段和公告／版本信息。
- 字段空值如实保留。各字段有限值覆盖率见 `data/ashare/audit.json`；例如
  `cash_flow_ratio_ttm` 约为 56.8%，不因数据集获取完成而视作所有字段均完整。
- 财报版本是供应商本次查询返回的历史版本证据，并非逐日归档的供应商数据库快照。
  基本面时点语义依赖 RQ PIT 口径，后续更换供应商或修订处理方式须生成新快照。

## A 股退市边界例外

RQ 成员表中的下列 23 个成员日位于 RQ 标记的退市日或之后，没有对应行情。
保留原始成员表，不补造价格。详表：`data/ashare/metadata/coverage_exceptions.parq`。

| 证券 | 缺行情成员日期 | 成员日数 |
| --- | --- | --- |
| 601299.XSHG | 2015-05-20 | 1 |
| 600270.XSHG | 2018-12-28 至 2019-01-17 | 13 |
| 600068.XSHG | 2021-09-13 至 2021-09-27 | 9 |

这类存续期外记录与未解决的下载缺口分开计数；只有后者阻止获取完成。

## 期货原始异常

下列 3 条记录的 open 超出供应商返回的 high/low 区间，单独重新查询结果一致。

| 真实合约 | Bar 结束时间（Asia/Shanghai） |
| --- | --- |
| TF1703 / CFFEX | 2017-01-04 09:20 |
| TF1703 / CFFEX | 2017-01-09 09:20 |
| TF1706 / CFFEX | 2017-04-13 09:20 |

原始文件不改写。MarketData 保留行位置，将异常数值输入置空并排除评分。
因此期货审计的严格原始完整性 `passed=false`，隔离策略下的 `research_ready=true`。
各字段原值见 `data/futures/audit.json`。
