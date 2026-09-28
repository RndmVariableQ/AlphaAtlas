# DSL 与自定义算子

## 文本与编译

```python
from alpha_atlas.expressions import compile_factor

factor = compile_factor(
    """
base = TS_MEAN($close, 20)
$close / base - 1
""",
    {"close"},
)
```

支持位置参数、四则运算、单一比较、中间变量、注释及末行输出。条件组合使用
`AND/OR/NOT/IF_THEN_ELSE`。不支持关键字参数、变量重新赋值、属性访问、任意 Python。
所有方法的公共 `get_context().expression_rules` 均提供 `syntax_rules` 和 `syntax_example`：
复杂公式建议用中间变量拆成多行，简单公式可保持单行。变量先定义后引用，末行是数值
输出表达式，不能只写赋值或 `return`。JSON 的 `expression` 字符串用 `\n` 编码换行，
不包含 Markdown 代码围栏，例如：

```json
{"expression": "base = TS_MEAN($close, 20)\n$close / base - 1"}
```

公共示例的 `$field` 需替换为当前允许字段；示例仅说明语法，不推荐因子或窗口。
中间变量会展开为规范 AST，等价的单行与多行写法具有相同因子身份，复杂度限制按展开后计算。

原生 5m 可用 `$field@15m/@30m/@60m/@1d` 查询已完成的高周期特征，见下文。
外部仍可构造 `Expression`；原 `mean/std/lag/zscore/csrank` AST 名称保留为别名。
`RANK`、`TS_PCTCHANGE`、`POW` 分别规范化为 `CS_RANK`、`RETURN`、`POWER`。

编译结果含规范化 AST、因子 ID、实际字段、语义依赖、窗口历史层数、节点数和深度。
`lookback` 累加各层窗口的额外 Bar 数；混合周期时单位随子树变化，不能当作统一的 5m
预热行数使用。平台仍按内部预热日期加载，实际完整窗口由对应周期执行器判断。
标量常数与静态参数分开校验，最终因子输出必须是数值。默认最多 20 层、100 个展开节点；
平台不设置窗口或累计历史需求上限，窗口必须是正整数且满足算子最小值。
未知字段、标签和结构字段不能作为算子输入。数据仍从训练开始前一年加载；日期仅保存在
平台内部和 run.json。窗口超过可用历史时返回 null，不补造行情、不跨合约或连续段。
超出整张面板长度的基础窗口直接返回 null，避免为不可能完整的窗口分配巨大临时数组。
四个符号基线自行使用有限候选窗口集合，这属于方法策略，不限制其他方法提交的窗口。

## 运行上下文与查询接口

模型可见的公共入口为 `session.query("get_context", {})`，一次返回 asset、frequency、
target、metric、fields、operators、expression_rules、evaluation_rules、reference_library。
实时 remaining_attempts 只出现在评估结果中；平台内部的 `session.get_context()` 保留它
用于 ask/tell 调度与预算停止，不直接序列化给模型。
目标定义包含 price_field、horizon_bars、return_type、boundary，不含日期或标签数组。
不向方法提供 run_id、snapshot_id、universe、fold、train/val/预热日期或窗口预算。

以下底层接口用于组装公共 context，不作为模型单独调用的工具：

- `get_fields()`：当前允许的字段名称。
- `get_evaluation_rules()`：评估聚合、训练方向、覆盖率、当前准入门槛与查重规则；无日期和数据值。
- `list_operators()`：当前基础及已成功注册算子的名称、签名、作用域和类型。
- `get_operator(name)`：完整 OperatorSpec、别名和最小窗口；未知算子明确报错。
- `get_expression_rules()`：单行／多行语法与 JSON 示例、节点/深度限制、通用数值与高周期规则，
  不包含窗口上限或日期。
- `get_library()`：已有的只读因子库；因子间训练统计仍经 `factor_correlations()` 获取。

内部 context 和目标定义不可修改；公共查询结果为独立副本，修改不会改变注册表。新增算子成功后
再次查询即可看到，失败算子不会出现。ask/tell 方法可通过 `set_session(session)` 绑定查询入口；
Runner 在运行和恢复时都绑定。AlphaProbe 从查询接口获取需要的信息，具体模型协议见
[AlphaProbe 查询](alphaprobe.md#模型只读查询)。不增加通用工具执行框架。

## 数值规则

- Float64；每个中间结果的 NaN/Inf 归一为 null。窗口包含当前 Bar，要求完整有效历史。
- 除数绝对值不超过 `1e-12`、非法对数/平方根/幂返回 null；0 的非正数次幂返回 null。
- `POWER(x,p)` 使用静态指数；`SIGNED_POWER` 为 `sign(x)*abs(x)^p`。
- 方差、标准差、协方差采用 ddof=1；偏度和 Fisher 超额峰度采用偏差修正。
- `TS_RANK` 为当前值平均秩/窗口长度；`TS_ARGMIN/MAX` 为距今 Bar 数，并列取最近者。
- `TS_CORR` 为 Pearson；`TS_RANKCORR` 在每个同一窗口内分别求平均秩后计算 Pearson。
- `TS_PROD` 保留负数和零；溢出返回 null。线性衰减权重从旧到新为 1…n，并归一化。
  `TS_LINEAR_DECAY` 支持原始或中间序列含 null；受缺失影响的窗口返回 null，
  待完整有效窗口重新形成后恢复输出。单个因子的 Polars 计算 panic 记为 `compute_error`，
  不中断后续候选评估；用户中断仍正常传递。
- 分位数线性插值。`TS_WINSORIZE(x,w,lo=0.01,hi=0.99)` 是内置组合，截断当前值；
  DSL 调用使用位置参数。要求 `0 <= lo < hi <= 1`。
- 条件缺失保持未知；`IS_FINITE(null)` 为 false，`IF_THEN_ELSE(null,...)` 为 null。
  `FILLNA` 不改变成员资格、行情质量或目标有效性。
- 截面只使用当前 timestamp 的 eligible 有限值。`CS_SCALE` 除以绝对值之和；
  截面标准差用 ddof=1，零分母返回 null。
- TS 按 `(exchange,instrument_id,segment_id)` 分组。原生 Bar 可跨正常休市；缺失交易日、
  已知无效行情、停牌和主力切换/重新进入重置。未建立完整历史日内时段表，不能识别所有缺 Bar。

基础清单见 [60 个算子](operators.md)。排名和极值等需要专用计算时使用有界数组批处理，
不把单个因子整体转换为 pandas，也不在窗口上调用逐行 Python 回调。

## 高周期特征与无前视广播

原生 5m 输入支持 `$close@15m`、`$close@30m`、`$close@60m`、`$close@1d`，远月写作
`$close_p1@30m`、`$volume_p2@1d`。允许字段仍使用基础名称，例如 `close_p1`；
MarketData 提供内部频率与连续段元数据，不能从时间戳间隔猜测数据频率。日线输入不支持
这些后缀，不将高周期聚合结果写回原生数据，也不改变目标的 5m horizon。

```text
# 20 根已完成 30m Bar 的均值，最后广播到每根 5m：
TS_MEAN($close_p1@30m,20)

# 两腿先在 60m 上计算比值与标准化：
TS_ZSCORE($close_p1@60m/$close_p2@60m-1,20)

# 混合周期，已完成的日线结果先广播，再与当前 5m 价格组合：
$close / TS_MEAN($close@1d,5) - 1
```

每个子树只有一种频率时，算术、条件、TS、CS 及自定义算子都在该频率上执行；
常数继承所在子树的频率。混合不同频率的子树在原生 5m 上计算，粗周期子树先完成运算再
向后匹配到 5m，而不是先重复填充原始字段再计算其高周期窗口。赋值和 composite 展开
不改变这些规则。`TS_MEAN($close@30m-$close@60m,20)` 因而是 20 根 5m 的均值。

| 字段（包括 `_p1/_p2`） | 桶内聚合 |
| --- | --- |
| open | 第一根 |
| high / low | 最大 / 最小 |
| close / open_interest / days_to_maturity | 最后一根 |
| volume / amount | 求和 |

`15m/30m/60m` 参照 ChaoticTrader 的自然时钟切桶和结束时间标签：15m、30m
桶最早在 09:30 可见，09:25 不能读取。午休等间歇不产生虚构 Bar；端点无观测时，
只能在端点之后的已有 5m 行上看到该桶。聚合实际观测，不要求午休桶也有 6/12 根 Bar。

`1d` 按供应商 `trading_day` 合并夜盘、跨午夜和白盘。当前导出没有历史收盘时刻表，
因此采用明确的确认时点：**同一真实合约下一交易日的首根已观测 Bar**，才发布上一交易日。
例如周五夜盘归属周一，周五夜盘首根 Bar 可以确认周五日线。末日未确认则不发布。
这比 ChaoticTrader 根据当前输入中当天最大时间戳标记日线完成更晚，但截断到盘中或
补入未来 Bar 时不会回改过去的结果。不能把当前输入的最后一行当作已知收盘时刻。

广播只匹配 `available_at <= 当前 timestamp`，同时要求主合约连续段与所有依赖腿的
连续段相同。换约、退出后重新入选、已知缺口都会阻断广播与滚动历史。桶跨越连续段时
相关聚合置空；桶内相关字段有 null/NaN/Inf 或已知异常，也置空。空桶结果作为一次发布
保留，不能跳过它继续携带更早的有效值。只涉及 p1 的计算不受 p2 换腿影响。
CS 在相同发布时间的合格观测上计算，其结果再向后广播。

现有源数据如果整根 5m 行缺失且没有日内交易时段表，无法区分它与休市间歇；
这项原生覆盖限制仍然保留。明确缺失的远月报价不会被当成有效输入。重复观测、空身份或
时间、合约内交易日倒退会拒绝执行，避免错误交易日将未来报价归入已发布的日线。

规范化 AST 保留后缀，`compiled.fields` 仅列实际基础字段；原有表达式身份不变。
沿用原有冻结、代码与快照校验。`get_expression_rules().timeframes` 提供频率、允许字段
和规则；ReAct 固定系统提示词包含这些信息，未添加运行日期或身份。

## 远月合约字段

当前主力真实合约保留 `open/high/low/close/volume/amount/open_interest`。其后按到期日排序
的第一、第二个合约分别使用 `_p1`、`_p2` 后缀，完整开放同样七个字段；另提供
`days_to_maturity`、`days_to_maturity_p1`、`days_to_maturity_p2` 三个日历到期天数字段。
后缀表示更远的到期月份，不表示持仓量排名。表达式可直接组合，例如：

```python
# 价差、相对持仓量及价差时序标准化
$close_p1 / $close - 1
# 单独提交另一个表达式：
# $open_interest_p1 / $open_interest
# TS_ZSCORE($close_p1 / $close - 1, 60)
```

每根样本仍由 `(exchange,instrument_id,timestamp)` 标识当前主力，目标仍是该真实合约
未来 12 根原生 Bar。原生行情只在 **exchange、实际合约、trading_day、Bar 结束 timestamp**
完全匹配时左连接到当前样本；不做 asof、不向前/向后填充、不使用下一根 Bar。缺报价保留 null。
夜盘使用供应商的归属交易日，不能仅按墙上日期拼接。主力 rule 0 使用上一收盘持仓量，
远月按历史当日已上市的合约与到期日选取，不使用当日最终成交量/持仓量或未来上市合约。

远月的实际 exchange/instrument_id 作为平台内部元数据保留，不允许输入 DSL。MarketData
分别隔离各腿的换约、缺报价和无效 OHLC，产生内部 `segment_id_p1/p2`；读取远月字段时要求
配套实际身份与 OHLC 元数据完整，不能只导入孤立的 close_p1 序列。每个 TS 子表达式按自己
依赖的腿追加分段：`TS_MEAN($close_p1,20)` 只受主力和 p1 边界影响；
`TS_CORR($close_p1,$close_p2,20)` 同时受两腿影响。边界依赖经算术、条件、嵌套窗口、
截面计算和自定义 group_batch 传递。纯主力窗口与目标不因辅助腿变化而重新分段。
辅助腿异常也不取消主力行的 eligible 资格，只将相关字段置空并重新预热。

适配器仍使用既有 `.parq` 分区、回执和 manifest；扩展数据保存为独立的
`data/futures_curve/`，不会覆盖 `data/futures/`。manifest 逐月记录选定腿的行数、可观测行数、
没有第二/第三个可用期限的行数和缺失 Bar 数。请求的辅助 Bar 有缺失时状态为 incomplete。
正式采集属于显式操作；测试只使用合成数据与模拟供应商。使用 `--merge` 在采集结束后，
将本次 manifest 中成功的分区合并到 `data/futures_curve/bars_5m.parq`，按时间、交易所和
合约排序，检查行数与观测键唯一性。分区保留用于断点续传，合并文件放在 `bars/` 之外，
避免 MarketData 重复读取。合并保留空值与 incomplete 状态，详情见 manifest 的 `merged_file`。

```powershell
# 显式采集原生 5m，并导出 p1/p2 字段；凭据方式沿用既有采集入口。
uv run python scripts/acquire_data.py futures --far-contracts --merge `
  --env-file D:\WORKSPACE\ChaoticTrader\.env

# 已有分区时只合并，不连接供应商、不读取凭据。
uv run python scripts/acquire_data.py futures --far-contracts --merge-only

# 当前快照的 2 项缺失已复查，显式接受 incomplete；仍通过允许列表限定 Agent 字段。
uv run atlas run --asset futures_curve --fold fold1 --method react --attempts 20 --allow-incomplete `
  --fields close,volume,open_interest,close_p1,volume_p1,open_interest_p1,close_p2
```

默认搜索不会自行扩大字段权限；`get_fields()` 与 ReAct 的固定系统规则会显示明确允许的
新字段及分段约定。远月字段沿用已有算子、字段查询和冻结机制；高周期后缀扩展见上文。

## DSL 组合

```python
from alpha_atlas.operators import OperatorDefinition, OperatorRegistry

registry = OperatorRegistry()
feedback = registry.register(
    OperatorDefinition(
        "REL_DEV",
        (("x", "series"), ("window", "window")),
        "x / TS_MEAN(x,window) - 1",
    )
)
assert feedback.accepted
```

组合只引用已有算子，编译时展开；手写展开式与组合调用身份相同。禁止覆盖已注册名称，
修改时使用新版本名称。组合没有额外网络服务或执行后端。

## 运行内 group_batch

生产内核使用 Numba `njit`，独立 `golden` 使用 NumPy；不需要 Docker 或镜像环境变量。
Runner 默认提供运行时。独立使用注册表时显式传入 `OperatorRegistry(runtime=NumbaRuntime())`，
其中 `NumbaRuntime` 从 `alpha_atlas.operators.runtime` 导入。

每次注册在一个临时本地子进程里完成编译和整套测试，默认 120 秒超时终止；通过后才在当前
运行编译并复用内核。只接受 nopython，没有 object mode 或 Python 生产回退。平台自动应用
`njit(nogil=True, boundscheck=True, fastmath=False, error_model="numpy")`，无需提交装饰器。
测试进程只接收 JSON 定义和配置，不接收行情/标签文件；不交换 pickle 或候选机器码。

这是可信本地代码模式，语法白名单和子进程不是安全沙箱。正式执行没有容器权限/内存隔离，
执行预算只在窗口/截面调用之间检查，不能强行中断正在运行的本地内核。配置见
`configs/operators.toml`；子进程注册超时是独立的硬性门槛。

```python
definition = OperatorDefinition(
    name="CUSTOM_DOUBLE",
    parameters=(("x", "series"),),
    kind="group_batch",
    scope="ts",
    history=0,
    body="def kernel(x):\n    return x * 2.0",
    golden="def golden(x):\n    return np.add(x, x)",
    examples=({"inputs": [[1.0, 2.0]], "params": [], "expected": [2.0, 4.0]},),
)
# 在 method.run(session) 中调用：
# feedback = session.register_operator(definition)
# feedback = session.evaluate(Candidate("CUSTOM_DOUBLE($close)"))
```

内核/参考函数分别名为 `kernel/golden`，签名与 parameters 一致，不带注解、默认参数或装饰器。
可写 `import numpy as np`，不能访问其他模块。仅开放有限 NumPy 数值函数，完整白名单位于
`operators/code_policy.py`；白名单内的调用仍必须通过当前 Numba 的编译支持检查。

`series` 参数为一维 Float64 数组；`window` 为正整数，`float` 为有限静态实数。
必须返回等长 Float64 数组，不修改输入。例子的缺失值用 JSON null；golden 不调用 kernel。

- TS 每次只收到截至当前的声明窗口，只有最后一个输出被采纳。`history` 为额外历史 Bar 数；
  若指定 `window_arg`，历史需求为该参数加 `history_offset`（默认 -1）。
- CS 每次只收到当前获准截面，不能声明历史需求。
- 正式求值在本地按时间前向调用已编译内核，不传入完整未来分组，不启动容器或子进程。
  输入为独立只读数组；窗口不足或非有限输入由公共层产生 null。函数只能使用局部状态，
  禁止可变全局状态；同一运行复用编译结果，但不复用上次计算的候选状态。

注册统一强制运行平台边界、独立参考、确定性、形状/类型及实际窗口路径的验证，候选无需
自行调用测试脚本。提交样例、实际分组输出和历史前缀都必须至少有一个有限值，防止全 null
空验证。每个提交样例同时对照 expected 和 golden；输入只读、等长 Float64 契约始终检查。

- TS：不同交易所的同名标的、不同合约、连续段分别对照窗口 golden；扰动其他组不影响
  被保护组，打乱输入行序不改变对应结果。
- CS：多个 timestamp 独立对照获准截面的 golden；排除不 eligible 及任一输入非有限的行，
  扰动被排除行不能影响结果。内核仅获得这些筛选后的数组。
- 因果性：分别比较追加数据、扰动未来数据前后的历史输出，前缀必须产生有效值。
- 性能增长：预热两种规模，交替运行并取五次内核耗时中位数。可变 TS 比较窗口 64/256，
  CS 比较截面 64/256；固定历史 TS 保持窗口不变，比较 64/256 次调用的总内核耗时。
  计时排除编译和输入转换；小规模耗时下限 1 ms。大规模耗时除以小规模计时下限后的耗时，
  再除以工作量增长比，超过 3 则拒绝。配置固定在 `configs/operators.toml`，并纳入运行指纹。
  这只是两种规模的性能探针，不能证明任意规模都高效；固定窗口探针也不衡量窗口增长复杂度。

`feedback.validation` 保存每阶段状态、耗时和错误；`performance_growth` 还保存测量规模、
中位耗时、增长比、计时下限和是否拒绝。记录先落盘，全部通过后才发布注册；失败保留已完成
阶段和失败阶段。冻结恢复注册也执行同一组检查。通过注册不代表通过因子质量和相关性准入。
当前没有 stateful、任意依赖安装或通用 LLM 工具。四个 ask/tell 基线及 AlphaPROBE
专用 LLM 搜索支持 JSON 检查点；
本节的任意 run(session) 算子创建流程暂不支持中断恢复。

## 复杂内核示例

完整复杂内核示例见 [examples/complex_operator.py](../examples/complex_operator.py)：
一个 TS 内核接收数值和成交量窗口，依次计算中位数/MAD、异常值截断、成交量与时间加权
最小二乘斜率、残差均方根，输出斜率/残差 RMS。权重为窗口内相对成交量乘以 1…window；
截断边界为中位数 ± 3 × 1.4826 × MAD。窗口少于 3、MAD 为零、负成交量、全零成交量、
退化回归或残差 RMS 不超过 1e-12 时输出 null。该分数是演示性趋势统计量，不是 t 统计量。

文件包含独立标量参考实现、手算样例、`run(session)` 接入及 TS 结果接 `CS_RANK` 的组合。
示例不新增基础算子，也不保证因子可以通过准入。安装项目依赖后运行：

```powershell
uv run python examples/complex_operator.py
```

只使用文件内合成数据；注册在子进程验证，正式执行复用本地 Numba 内核。集成测试包含该示例的
手算数值、异常值截断、退化输入、输入乱序、历史不变性以及获准截面的排名。

## 证据与重现

期货 `EvaluationReport.metrics` 按主指标在前排列：Pearson 的 SQRT 成交额加权、品种等权，
然后是 Spearman 的 SQRT 成交额加权、品种等权；每项按 train/val/val_raw 排列，共 12 项。
两种相关均先合并同品种合约样本，Spearman 重新排名，Pearson 直接使用因子与标签数值。
val 统一使用加权 train Pearson IC 确定的方向。库筛选和方法反馈只用主指标；完整口径见
[统一评估](../README.md#统一评估与公平对比)。`context.metric` 标明主指标。
成交额由平台通过 MarketData 额外读取用于固定区间权重；不因此扩大方法的允许字段。
新 `oos.json` 每个成功结果都使用 `metrics` 数组：期货四项、A 股两项；A 股仍然是
Spearman 在前、Pearson 在后，train/val 报告共六项。旧单项 `metric` 仍可只读展示。
控制台按加权方式分表，最右两列为 Spearman IC 与 Pearson IC；旧记录缺失 Pearson 显示 —。
`atlas test <run目录>` 的控制台默认显示表格，`--json` 可切换为 JSON 输出；不改变落盘格式。
查看已经保存的历史结果用 `atlas report <run目录> --oos`；只读 oos.json 并标明未重新验证，
不触发行情读取、计算或写入。该展示入口不改变 test/resume 的指纹检查。

每个 trial 的评估、准入和库版本写入同一个 JSON 后才更新内存；成员值缓存不能单独证明入库。
`RunStore.library_view()` 从已提交 trial 重建只读成员。缓存可能被回收；单个当前求值结果的工作
内存不受缓存容量限制，避免无法返回刚计算的观测。

Runner 的 `cache/evaluations` 与 `cache/members` 共用 512 MiB 磁盘上限。启动/恢复只列举
这两个目录的 Arrow 文件大小和修改时间，不读取全部 IPC 内容；运行中增量统计，超限淘汰
最久未使用的文件，恢复时以修改时间初始化顺序。每次成功写入完成后，保留文件总量不超限；
写入临时文件期间可能短暂超限。单个文件超过上限时不保留磁盘副本，当前观测仍可用于计算。
恢复只重建成员目录，查重需要的数值缺失/不可读时才重算并核对原评估，不重复 trial 或准入。
内存缓存继续使用既有预算，不是进程总内存限制。独立装配可将同一 `ArrowCache` 传给评估器
和因子库；因子库同时提供 `rebuild(entry)` 回调。未提供缓存对象的独立因子库仍按显式目录
保存成员值，调用方自行管理保留范围。

冻结保存全部成员、展开计划、注册定义、方向及 Python/NumPy/Numba/llvmlite 和平台版本。
运行汇总为根目录 report.md，OOS 原始结果为同层 oos.json；报告只从现有记录生成，不触发计算。
OOS 先检查配置、代码/锁文件、
数据和冻结文件，再读取缓存报告；成员失败不会静默省略。
同步 ask/tell 搜索恢复见 [README](../README.md#中断与恢复)：复用已提交反馈，缺失成员缓存
可重建，方法状态和随机数状态由 JSON 检查点恢复。任意 run(session) 和通用 LLM 流程尚不支持；
AlphaPROBE 的三阶段响应与待处理子代恢复见 [搜索适配说明](alphaprobe.md)。

旧 Docker 运行产物保留，但不自动迁移到 Numba；旧运行时或指纹不符时明确拒绝重现。
