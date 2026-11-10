# Zookeeper：26/37 个商品特征的 RiceQuant 适配

这是固定公式目录及其可复现计算器。历史 baseline 为 `zookeeper_rq_monthly_v1`；
可执行月频 DSL 配方为 `zookeeper_monthly_dsl_v2`。
覆盖论文 37 个 commodity-specific 特征中的 26 个；不是论文 81 个总特征的完整复现，
也不是原论文海外市场、月度多空策略或 Zookeeper 组合的收益复现。

来源：Fan、Li、Qiao、Zhang，*Call the Zookeeper: A Unified Framework for Commodity Risk
Premiums*，SSRN 6618461，2026 年 4 月，附录 Table A1（PDF 55–56 页）、BGR 模型说明
（PDF 28 页）。[论文入口](https://ssrn.com/abstract=6618461)。本地公式实现依据论文说明，
没有复制作者代码；歧义处采用下述明确约定，不把本地选择称为作者的精确定义。

## 文件与读取

```text
zookeeper/
  factors.jsonl       26 条自描述定义；每行一个 JSON 对象
  factors_v2.jsonl    26 条可执行月频 DSL；另列 5m published_expression
  pipeline.json      日频 DSL、自然月归约、滞后组合收益
  excluded.jsonl     其余 11 项及缺失原因
  __init__.py        definitions()、candidates()，不访问行情
  compute.py         日观察 → 自然月信号 → 下月 5m 行对齐
  dsl.py             公共 DSL 执行器运行 v2 配方，独立于历史 compute.py
  README.md          数据、时间、复现与增量实验接口
assets/zookeeper.py  只读 MarketData 适配，复用现有本地快照
scripts/evaluate_zookeeper.py  train/val → 冻结 → 独立 test
```

`factors.jsonl` 是描述、公式、字段依赖、窗口、可用时点和适配说明的权威目录。
`expression` 是准备后的普通 DSL 字段引用，如 `$zk_mom12`；该字段不是 RiceQuant 原生字段，
必须先通过适配器计算。`source_fields` 描述计算所需的源字段，不表示 Agent 获得供应商权限。

```python
import json
from pathlib import Path

path = Path("src/alpha_atlas/factor_libraries/zookeeper/factors.jsonl")
factors = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
by_name = {factor["name"]: factor for factor in factors}
print(by_name["mom12"]["formula"])
```

```python
from alpha_atlas.factor_libraries.zookeeper import candidates, definitions

metadata = definitions()  # 每次返回独立副本
catalogue = candidates()  # 26 个普通 Candidate；不表示通过准入
```

这是一份独立实验参考目录，不自动修改 `benchmark.toml` 或其他方法的参考库权限。
公式、方向和本次实测表现分文件保存，方法脚本可以只读取定义而不读取 test 反馈。

## 可执行配方与实验版本

`factors_v2.jsonl` 使用 schema_version=2。`expression` 是完整月频计算式，
如 `TS_PROD(1 + $R, 12) - 1`；输入 `$R` 是配方生成的月收益，不能直接用于原始 5m 面板。
`published_expression` 为 `$zk_mom12`，由适配器把上月结果发布到本月 5m 行。
日频偏度、峰度与 250 日波动先通过公共 DSL 计算，再按自然月 last 归约。
CV 用月度日收益和、平方和及计数还原 12 月的日收益方差/均值，保持原样本方差口径。
BGR 的组合收益使用上月成员与排序，完整 12 月三因子 OLS 通过公共算子执行。

```python
from alpha_atlas.factor_libraries.zookeeper.dsl import compute_monthly, recipe

plan = recipe()  # JSON 兼容，只含定义，不读行情或目标
expressions = {row["name"]: row["expression"] for row in plan["factors"]}
# base_daily / curve_daily 必须来自资产适配层，且截至已知时点。
monthly = compute_monthly(base_daily, curve_daily, plan=plan)
```

新增的 6 个 DSL 算子及面板聚合接口见[DSL](../../../../docs/dsl.md)。
26 项仍是适配代理；复用算子不使其自动成为论文精确复现。
`uv run python scripts/validate_zookeeper_dsl.py` 可在本地数据上逐列检查 v1/v2 的数值与
缺失位置；默认截至 2023 年末，只比较特征，不计算目标或 OOS 绩效。

```bash
uv run atlas run --config rq8_zk26_fold2_v2 --method random --search-only
uv run atlas finish artifacts/runs/<run_id>
```

默认字段为八字段加 26 个发布字段，共 34 个。方法可组合它们或调用公共算子；在 5m
方法里滚动 12 行仍是 12 根 5m，不能视为 12 月。准备公式及该限制随公共 context 提供。
本入口运行普通方法的搜索与准入，最终模型使用其准入库；它不会把所有 26 项强制入库。
固定全 26 项的历史 Linear/LightGBM baseline 仍使用下方原实验及已有产物。
版本固定范围、数据边界和复跑命令见[研究协议](../../../../docs/research_protocol.md#数据与算子版本)。

## 数据及代理边界

| 组 | 数量 | 当前输入 |
|---|---:|---|
| 价格、风险、量与持仓 | 16 | Kernel 八字段中的 adj_vwap、volume、amount、open_interest |
| 期限结构 | 9 | futures_curve 的 close、close_p1、close_p2 及真实合约身份 |
| BGR 残差风险 | 1 | 前两组派生的月收益、基差和十二月动量 |
| 排除 | 11 | 七个 COT 类特征、四个通胀/汇率暴露；见 excluded.jsonl |

需要本地 `data/futures_kernel_5m_full/` 和 `data/futures_curve/` 的 manifest、行情分区
及已有交易日/主力元数据。适配器不下载数据，不修改快照，不读取其他项目数据库。
八字段快照是此前指定的 Kernel 原始 1m 聚合 5m 数据，不能称为 RiceQuant 原生 5m。
目标仍使用该快照的 `adj_close`，与八字段实验一致。

日价格代理为 `sum(adj_vwap * volume) / sum(volume)`，分子、分母都只使用有限、正值
VWAP 且成交量为正的同一批 Bar。按供应商 `trading_day` 汇总夜盘和日盘；没有按自然日期
拆夜盘。源 VWAP 已含代理、钳制和无成交空值；这个日值不是交易所官方结算价。
日成交量/额为日内求和，OI 为最后一根 Bar 的值；缺失不以前值填补。
日/月聚合仅用于特征计算；对齐后的输出保留原 5m 行，不替换行情或目标。

曲线使用原生 5m 同时刻的未复权收盘价，独立计算比值，不把复权主力和未复权远月相除。
其三条腿是“主力及其后两个到期月份”，不是论文“第一、第二、第三近月”。
曲线不要求与 Kernel 的主力规则一致：它作为同品种另一套历史曲线来源，按 exchange/product
及交易日、月份对齐；没有独立认证两套上游主力规则完全相同，这是本版代理的一项限制。

当前曲线 manifest 为 `incomplete`，记录 2 个辅助 Bar 缺失，另有当时未上市的远月腿。
本适配器显式读取现有观测并保留空值；不把该清单改成 complete。
因此本入口属于固定目录的**诊断性评估**，准入栏表示应用现有门槛的审计结果，
不把不完整辅助快照注册成正式研究数据。正式公平比较应先取得完整且审计通过的曲线快照，
再建立新运行；不能把旧 test 结果用于改公式后继续声称盲测。

### 整合层次与 PIT 保证范围

当前整合发生在特征适配层：两份原始快照分别读取，日/月度计算按 `(exchange, product)`
连接；26 个上月特征再对齐到 Kernel 原来的 5m 行，保留 `row_id`、真实合约及目标身份。
16 项用八字段，9 项用曲线，1 项 BGR 同时用两者。两个来源的主力合约可能不同，
不能把曲线的 `close_p1` 直接视为 Kernel 主力的下一期限，也不能把复权主力与原始远月相除。
目前没有合并原始 Parquet，也没有把 `_p1/_p2` 开放给原八字段 asset 的所有方法。
可显式读取八字段与 `zk_*` 的同一张矩阵；`ZookeeperMarketData.get_fields()` 默认只列 26 项。

计算层采用已完成月份、下月可用、历史持仓与完整窗口，并通过前缀／未来扰动测试。
这不等于底层数据具备完整历史版本 PIT：Kernel 导出 SQL 按 `received_at DESC, ingest_seq DESC`
选择最新 `final` 记录，没有 `received_at <= 当时决策时点` 条件，导出也未保留逐时点版本。
`is_dominant` 与 `adj_cum_factor` 来自 Kernel 既有结果，现有快照没有足够元数据独立证明其
当时生效时间；不把“已复权”自动解释成“历史版本 PIT 已认证”。详细证据见
[baseline 核验记录](../../../../docs/records/zookeeper26_fold2/report.md#baseline-与时点核验)。
本库因此是**固定 final 快照上的因果计算与历史诊断 baseline**，不是完整 vintage-PIT 认证。

26 项复杂公式由 `compute_monthly` 的 Polars／NumPy 实现，DSL 冻结表达式均为 `$zk_*`
字段读取，不含高周期或 TS 算子依赖。未使用 `@1d` 冒充官方结算价，也未把月数当 5m Bar 数。
通用 `$adj_vwap@1d` 的下一交易日确认与广播另由平台高周期测试覆盖，不是本目录的公式路径。

## 日历、缺失和数值语义

- `r_d = P_d / P_(d-1) - 1`；只在相邻观测交易日计算。八字段价格已经比例复权，
  正常主力换约可以延续特征；已知缺日断开日收益及 250 日窗口。未编造连续价格或目标。
- 月收益 `R_m` 复合当月日收益，要求当月每个观测交易日收益有效；历史首月通常为空。
  月末价格、量、OI、统计量形成月度信号。信号只在**下一交易月份**的第一根观测 5m Bar
  起可用；同月内保持不变。缺失月份不跨月前向填充。月末夜盘按所属交易日处理。
- 月窗口为自然月，日窗口为实际交易日。按所有已落地历史预热，不把 60 月缩成
  60 根 Bar 或一年历史。没有上市历史的品种保持空值；不按 test 覆盖率筛掉品种。
- 日标准差、日方差、残差标准差用 `ddof=1`。偏度为总体三阶标准化矩；峰度为总体
  四阶标准化矩减 3。`var` 遵循论文文字，计算**标准差**；`cv` 遵循文字计算**方差/均值**。
- `val` 的 42–54 月前历史均价固定为 13 个历史月末日 VWAP 的等权均值。
  `dOI` 固定为绝对差；`dmaOI` 为当前 OI 相对此前 12 月均值的比例偏离。当前月不进入均值。
- `iliq` 固定为月内 Amivest 流动性均值的倒数。日流动性是人民币成交额除绝对日收益，
  零收益、非正成交额或非有限值排除；有效天数至少为当月的 80%，且至少 2 天。
  没有将人民币量级伪装成论文美元量级。
- `basis = F0/F1`，`rbasis = (F0/F1)/(F1/F2)`；不按到期天数年化。
  `l2rbasis` 固定滞后原始 rbasis，另两项 l2 固定滞后对应十二月均值。
- 曲线月收益 `Cj` 只复合同一腿在相邻交易日仍为**同一真实合约**的日收益。
  换腿日和无效日不计算收益，要求每月有效比例 ≥80% 且至少 2 天；不足整月为空。
  这是“部分已观测同合约收益”的代理，包含在报告的适配限制中；不是完整可交易月收益。
  `bmom` 为两条腿各自 12 月复合收益之差；不把换腿价格跳变当作收益。
- 分母绝对值 ≤1e-12、非有限结果、历史不足均为空。内核不裁剪信号；统一评估器
  仅用 train 拟合因子和标签裁剪边界，val/test 固定使用这些边界。
- 月度诊断中保留 `return_count/day_count` 和两条腿的 `curve_return_coverage*`，便于审计。
  现有源数据没有完整日内应有时段表，因此不能宣称发现了所有缺 Bar。

## ivolaBGR 的明确本地选择

先构造月度三因子收益：AVG、BASIS、MOM。AVG 对上个月价格和月收益均有效的品种等权；
BASIS/MOM 使用上个月的 basis/mom12 排序，取上下各 `floor(N/4)` 个品种等权高减低，
N 至少为 4。并列时按 `(exchange, product)` 排序，持仓不根据当月实现收益是否有效来挑选。
每个组合至少 80% 原成员的当月收益有效时，才对有效成员重新等权；否则组合收益为空。
缺失成员不以排名外品种替补。这是已观测收益的风险因子代理，不是完整可交易组合的净值。
品种范围沿用当前 all 配置，包含股指、国债，因此也不等同于原文 25 个海外商品。

每个品种在最近 12 个自然月内对 `[1, AVG, BASIS, MOM]` 作 OLS，再计算该窗残差的
样本标准差。必须 12 月完整且矩阵秩为 4；不降维、不以其他风险模型替代，不用目标标签。
论文未明确滚动回归的具体窗口和若干组合细节，这里选择的 12 月回归、四分位、并列规则
均为本地固定适配，不声称与作者未提供代码逐位一致。

## 复现 train / val / test

依赖使用仓库 Python 3.13 和 `uv.lock`，先 `uv sync`。需要有权访问并准备上述本地数据；
GitHub 仓库只包含代码、定义和汇总指标，不包含授权行情、论文 PDF 或供应商凭据。
仅拥有本仓库不等于已经取得 RiceQuant 数据权限。

```bash
uv run python scripts/evaluate_zookeeper.py --fold fold2
```

也可以分阶段检查冻结物：

```bash
uv run python scripts/evaluate_zookeeper.py --fold fold2 --stage freeze
uv run python scripts/evaluate_zookeeper.py --stage test --run artifacts/reference-evaluations/实际运行目录
```

fold2：train 2019–2021、val 2022–2023、test 2024–2025。
目标、合约隔离、主指标、四种 IC、train 方向、train 裁剪、覆盖率和准入去重规则复用
[平台研究协议](../../../../docs/research_protocol.md)。本次仅改变固定输入因子和必要的全历史预热；
不把月度持有信号解释为日内交易策略收益，也不与旧未裁剪运行作严格公平比较。

输出在独立 `artifacts/reference-evaluations/zookeeper26-*/`：

| 文件 | 用途 |
|---|---|
| run.json | 实际配置、输入快照、适配版本、日期与预热策略 |
| factor_trials/*.json | 每项 train/val 评估与门槛审计 |
| frozen/library.json | 全 26 项定义、表达式身份、train 方向和裁剪边界；准入不筛选 test 集合 |
| oos.json | 冻结后全部因子的原始/定向 test 及逐年 IC、覆盖率和样本量 |
| results.jsonl | 每因子一行的 train/val/test 定向结果，便于其他实验脚本读取 |
| summary.json / report.md | 汇总与逐因子报告 |

派生月度特征和缺失诊断存于 `data/zookeeper/运行目录/` 的 `monthly_train_val.parq` 与
`monthly_through_test.parq`，路径记录在 run.json；行情及派生值不进 Git。

test 阶段检查现有配置指纹、快照、冻结文件及定义内容。源码指纹只记录来源。
准备阶段截至 val.end，test 行情和标签只在全目录冻结后加载；未来特征不进入搜索评估器。
逐年 OOS 是描述性拆分，不用于改方向或挑年份。

### Linear / LightGBM 组合预测

对同一个冻结目录执行公共模型入口；第一次只拟合 train+val，第二次读取已保存模型评分：

```bash
uv run atlas model artifacts/reference-evaluations/实际运行目录
uv run atlas model artifacts/reference-evaluations/实际运行目录 --test
```

两个模型使用全 26 项、原训练方向和 `configs/model.toml` 固定参数，不按准入结果或
单因子 Test 表现筛选。预处理在 2019–2023 年合并集拟合；测试为 2024–2025 年。
单因子评估中的 train 裁剪边界不直接用于模型；模型另拟合全局 P1–P99、均值和标准差，
Test 标签保持原始目标。部分缺失按拟合均值填补；所有有效列都缺失的行不预测。
完整规则见[模型协议](../../../../docs/model.md)。

产物为 `model/fit.json`（配置、因子顺序、预处理、两模型参数）、`model/test.json`
（四种 IC 和覆盖率）、`model/report.md`。复跑命令验证冻结身份和数据快照后命中缓存，
不覆盖模型，不重新训练。这里仍是历史 OOS 诊断，不是新的盲测或交易收益回测。

## 后续增量测试

用户指定的固定对照是
[baseline.json](../../../../docs/records/zookeeper26_fold2/baseline.json)：引用该次全部 26 项、
Linear 和 LightGBM 的已保存模型及其参数，不根据两者已知 Test 分数只保留较优模型。
该 JSON 只记录基准引用；参数和分数仍以其指向的 `fit.json`、`test.json` 为准，
路径相对该 JSON 所在目录。它不会改变方法搜索时 `benchmark.toml` 中的参考库权限。

其他脚本可读取 JSONL，或通过同一 MarketData 获取已对齐矩阵。它不会自动读取 test：

```python
from datetime import date
from pathlib import Path
from alpha_atlas.assets.zookeeper import ZookeeperMarketData

with ZookeeperMarketData(Path.cwd()) as market:
    fields = list(market.get_fields())
    train_val = market.load_features(fields=fields, start=date(2019, 1, 1), end=date(2023, 12, 31))
```

`train_val` 保留原快照的 `row_id`、真实合约、时间、连续段、eligible 和目标所需价格。
因此可将挖掘因子值按 `row_id` 做一对一合并，建立“固定 26 项”与“固定 26 项 + 新因子”
两组输入，再用同一模型与相同训练协议比较。两组需固定相同评分行和缺失处理，
避免把样本变化当作增量。不要把月频值直接和五分钟标签按行号拼接，
也不要只选本次 test 表现好的基准成员。真正的预测增量需另做冻结实验，单因子正 IC
本身不证明条件增量；本目录不预先声称增量有效。

已公开查看的 2024–2025 年结果属于历史 OOS；后续基于这些结果开发的方案需要新的未触碰
区间或事先固定的实验设计，不能将反复使用的 test 仍称为最终盲测。

## 已完成的固定目录评估

[fold2 诊断报告及可发布 JSONL 结果](../../../../docs/records/zookeeper26_fold2/report.md)
记录全 26 项冻结后的 OOS、输入分组和年度表现；实测结果独立于定义文件，
不导入其他方法的模型上下文。

[Linear / LightGBM 历史 OOS](../../../../docs/records/zookeeper26_fold2/model/report.md)
另保存组合基准的完整模型、预处理和 Test 指标，可用于后续事先固定的增量实验设计。
