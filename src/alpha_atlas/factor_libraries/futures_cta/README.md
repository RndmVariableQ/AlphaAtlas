# Futures CTA：可选的统一 DSL 基础因子定义库

本目录提供 22 个基础因子定义，供公共研究工具检索与公式改写。
它们都是现有 `Candidate` 接受的文本 DSL，使用相同编译器、AST 因子 ID、算子、评估与准入规则。
仅显式调用 `load_library("futures_cta", ...)` 时加载；`load_library(None)` 表示无基础库。
没有独立数值引擎、数据下载、Parquet 读取、自定义算子注册或新的身份/哈希机制。

来源：[QuantSkills / skill-futures-cta-alpha](https://github.com/quantskills/skill-futures-cta-alpha)，
固定参考版本 `a9c1feddaec41984d58e8a15ae5678743c769c2a`，主要参考
[compute_factors.py](https://github.com/quantskills/skill-futures-cta-alpha/blob/a9c1feddaec41984d58e8a15ae5678743c769c2a/scripts/compute_factors.py)。
本地适配版本：`futures_cta_dsl_v2`。原作者为 2026 QuantSkills contributors；本目录的适配
保留 GPL-3.0-only 声明及 [LICENSE](LICENSE)。这是公式适配，不是原库日线连续合约绩效复现。

## 加载与查询

```python
from alpha_atlas.factor_libraries import load_library

empty = load_library()  # None；不导入可选定义模块
library = load_library("futures_cta", frequency="5m", bars_per_day=1)

# 原库的日 Bar 直接作为一根 5m；20/63/252 就是对应根数的 5m。
allowed_fields = {"close", "high", "low", "open_interest"}
definitions = library.inspect(allowed_fields)  # 全部 22 项，含 DSL、来源、适配说明和缺失字段
candidates = library.candidates(allowed_fields)  # 此 allowlist 下 10 个普通 Candidate
```

`ReferenceLibrary` 与 `FactorDefinition` 为不可变对象。`inspect()` 返回独立的元数据副本；
因子定义不包含行情、标签、日期区间、fold 或运行身份。需要按名称查询时可直接筛选这 22 项。
`candidate.expression` 可以原样传给现有 `compile_factor()` 或 `session.evaluate()`，也可由
Agent 修改后提交；查询定义本身不读取数据、不评估、不入库。

可使用 `dataclasses.asdict(library)` 序列化完整定义及来源版本。改变窗口后的身份仍由原有
DSL 编译器生成，源因子名只用于追溯。缺少字段的定义仍可查阅，`candidates()` 会跳过这些定义，
排除原因在 `inspect()` 的 `missing_fields` 中明确列出。字段是否存在由显式 allowlist 决定，
不会自行探测供应商或扩大方法字段权限。

## 5m 窗口约定

- `frequency="1d"` 使用原始名义日窗口，`bars_per_day` 固定为 1。
- `frequency="5m"` 必须显式提供正整数 `bars_per_day`，不猜测夜盘或品种时段。
- 普通 N 日窗口换成 `N * bars_per_day` 根原生 Bar。例如尺度 48 下，20/63/252 日分别是
  960/3024/12096 根 Bar。公式中的静态窗口最终全部是整数，没有第二套表达式语法。
- 波动率使用一根原生 Bar 的对数收益、`20 * bars_per_day` 长度的样本标准差，并乘
  `sqrt(252 * bars_per_day)`。这是名义年化的 Bar 波动率，不是每日收盘收益的波动率。
- Carry 的 `days_to_maturity`、`days_to_maturity_p1` 始终为日历到期天数，
  年化系数 365 不参与 Bar 换算。尺度 1 的波动率保留源公式 sqrt(252) 缩放，
  不能解释成真实 5m 年化波动率。
- 截面使用现有 `CS_ZSCORE`：同 timestamp 的 eligible 标的。上游代码也用 z-score，虽然
  因子清单文字描述为排名。多合约同时 eligible 时是合约截面，不自动按品种去重或拼接。

这是统一尺度的近似适配。不同品种的夜盘长度、休市与缺 Bar 会让相同窗口覆盖不同数量的
实际交易日；需要把 `bars_per_day` 固定为实验条件，不能宣称精确按日对齐。保持原生 5m 数据，
不重采样、不产生替代数据集。所有窗口仍遵守真实合约/连续段隔离与完整有效窗口规则。
252 日等长窗口在单合约历史不足时会返回 null，是否满足覆盖率和 IC 门槛交给现有评估器。

## 定义及字段

窗口名称保留来源的名义日数；`inspect()` 给出换算后的完整公式。

| 本地名称 | 族 | 必需字段 |
|---|---|---|
| tsmom_252 | 时序动量 | close |
| tsmom_252_21 | 时序动量 | close |
| tsmom_63 | 时序动量 | close |
| breakout_55 | 突破 | close, high, low |
| sma_xover_20_100 | 均线交叉 | close |
| xsmom_252_21 | 截面动量 | close |
| xsmom_63 | 截面动量 | close |
| carry_ann | Carry | close, close_p1, days_to_maturity, days_to_maturity_p1 |
| roll_return_63 | 展期 | roll_return |
| basis_mom_20 | 基差 | basis |
| vol_scaled_carry | Carry | close, close_p1, days_to_maturity, days_to_maturity_p1 |
| ts_slope | 期限结构 | close, close_p1, days_to_maturity, days_to_maturity_p1 |
| ts_curvature | 期限结构 | close, close_p1, close_p2 |
| oi_price_confirm_20 | 持仓 | close, open_interest |
| broker_net_chg_5 | 席位 | broker_net, open_interest |
| ls_ratio_z | 持仓 | ls_ratio |
| virtual_ratio_chg | 持仓 | virtual_ratio |
| inventory_mom_20 | 库存 | inventory |
| receipt_mom_20 | 仓单 | warehouse_receipt |
| spot_profit_z | 现货利润 | spot_profit |
| lowvol | 波动 | close |
| st_reversal_5 | 反转 | close |

现有基础 DSL 没有 EMA。此处明确改为 `TS_MEAN`，并将本地名称改为 `sma_xover_20_100`，
`source_name` 保留 `ema_xover_20_100`。没有用新算子扩展这一适配。
上游 `oi` 显式映射为 `open_interest`。v2 以当前主力为起点，将前两个更远到期合约的字段
统一记为 `_p1/_p2`；不再提供 `pn/pf/Dn/Df/p1/p2/p3` 这组专用别名。
四个期限结构公式因此生成新的正常 DSL 身份，其余公式身份保持；旧 v1 冻结产物保留。
选择、时点对齐、缺失与合约切换规则见 [远月字段](../../../../docs/dsl.md#远月合约字段)。
库存、席位、现货等数据应在适配器按实际可得时间对齐，不能把收盘后公布的值填回当日早盘。
`roll_return` 必须是逐原生 Bar 的展期收益，不能将一个日值重复到各根 Bar 后求和。

## 实验接入

`configs/benchmark.toml` 的 `reference_library` 统一控制公共 `library_search` 访问，
设为空字符串关闭。定义按需加载，当前查询尺度为 `bars_per_day=1`，不会预先写入正式库。
提交评估仍消耗尝试预算，准入与冻结遵循[实验规范](../../../../docs/research_protocol.md)。

比较“无参考库 / futures_cta”时固定本库版本、换算尺度与允许字段，披露参考权限与成本。定义数 22、
当前字段可计算数、成功评估数和最终准入数是不同的数量；不把缺失数据或未通过准入算作入库。
原库的先验方向与历史 IC/收益结论不导入；方向仍由平台 train 确定，OOS 仍只在冻结后评估。

## 验证

`tests/test_factor_libraries.py` 在离线合成数据上检查全部 22 个适配公式的独立 NumPy 数值，
以及窗口换算、缺失字段、普通 Candidate/AST 身份、惰性加载、合约/交易所/连续段隔离、
完整窗口、零分母、历史因果性和截面 eligible 过滤。测试不读取凭据、供应商或真实行情。
已完成一次真实数据固定目录评估：fold1、`bars_per_day=48`，现有数据可计算 10 项，另 12 项
缺少输入字段。10 项按当前规则均未入库，其中 9 项首先被验证覆盖率门槛拒绝，短期反转被
质量门槛拒绝；冻结后仍评估全部 10 项的 test。详见
[本次报告](../../../../artifacts/reference-evaluations/futures-cta-fold1-bpd48-ee9e08e65b/report.md)。
这次 test 结果不用于调整方向、窗口或选择基础库成员。

固定目录评估入口（使用已落地的原生数据，不调用供应商）：

```powershell
uv run python scripts/evaluate_reference.py --fold fold1 --bars-per-day 48
```

结果保存至 `artifacts/reference-evaluations/` 下的独立目录。脚本按当前可用字段固定候选清单，
用现有 EvaluationService 与 FactorLibrary 记录 train/val 和准入结果，再冻结全部可计算定义。
冻结之后才加载 test，使用相同主指标与 train 方向计算定向及原始 IC。即使准入失败也保留
test 结果；该清单是固定公式评估集合，不表示正式入库成员。报告逐区间列出覆盖率及缺失字段。
脚本复用既有配置、快照和冻结校验；源码指纹仅记来源，不改变 `atlas run` 行为。
采集远月数据之后，可加 `--asset futures_curve --bars-per-day 1` 评估新的字段版本。
