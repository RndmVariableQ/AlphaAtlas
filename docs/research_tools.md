# 公共研究工具

实现位于 [session.py](../src/alpha_atlas/session.py)，所有方法复用同一个 SearchSession。
五个只读工具由 `session.query(name, arguments)` 分发；evaluate 由 runner 调用
`session.evaluate(Candidate(...))` 原子提交，再通过 `evaluation_result` 生成模型反馈。

## 工具定义

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `get_context` | 无 | 稳定研究元数据 |
| `library_search` | query="", source="all", offset=0, limit=10 | total、offset、limit、reference_enabled、results |
| `library_get` | factor_id | 成员候选、报告和版本；不存在时为 null |
| `library_list` | offset=0, limit=20 | total、成员 ID、候选及主验证指标 |
| `library_stats` | 无 | 本运行正式库统计 |
| `evaluate` | expression, hypothesis="", name=null | 指标、准入／拒绝原因、相关性与剩余尝试数 |

`library_search.source` 为 run/reference/all；按空白拆分关键词并全部匹配，忽略大小写。
已启用的参考库名称也可作为 `source` 的明确别名：当前启用 `futures_cta` 时等同于
`reference`。参考库关闭时不接受该别名，也不会因此加载或开启参考库；`library` 不作猜测映射。
查询文本最长 1000 字符，offset ≥ 0，limit 为 1–20；library_list 的 limit 为 1–100。
非法参数或未知工具显式报错，不扣评估预算；query 不分发 evaluate。搜索条数超限时
提示使用 offset/limit 分页，不静默截断或扩大上限。

五个只读工具的公共名称与使用说明集中在 `session.py` 的 `READ_ONLY_TOOLS`，供各方法
提示词和参数错误反馈共用。`query` 要求参数为对象，按实际函数签名检查缺失和未知参数；
错误带工具名、具体原因及该工具的参数说明，沿用调用方原有 `error` 字段和有界修复流程。
不转换含义不明确的枚举、数字字符串或未知字段，不改工具返回与检查点格式。
`library_get.factor_id` 必须是非空实际因子 ID；`trial:N` 是历史引用，明确拒绝，
普通未知 ID 仍返回 null。所有校验错误都不查询 OOS、不生成 trial、不扣评估预算。

`SearchSession.get_trial(factor_trial_index)` 是方法代码的内部读取能力，不属于 JSON 工具。
它只按当前运行的编号读取已提交原子 trial，返回候选及评估、准入事实，不附加当前预算，
不读取行情、不计算指标、不重新准入。`RunStore.trial` 直接定位单条文件，不扫描目录；
旧字段在原有读取入口解析。缺失、损坏、编号不匹配及缺少评估报告时明确报错。
三 Agent 的记录与使用方式见[研究计划记录](methods/annealing.md)。

run 只查询本次正式成员，包含 train/val 证据；reference 返回定义、来源版本、字段与
missing_fields，没有当前绩效或免费准入。参考库权限由 `benchmark.toml` 控制。
当前参考公式 `bars_per_day=1`，原日窗口数字按原生 Bar 解释；跨频计算使用显式后缀。

实验预置的初始库属于 run 成员，原有查询即可读取；它与无绩效参考定义不同。
`library_get/list` 的 `origin` 区分 initial/search，`library_stats` 的 initial_members 和
new_members 区分初始与新增数量。`get_context.initial_library` 在存在初始库时说明成员数
及其参与准入查重的规则，不提供来源运行 ID、日期或 Test；成员报告只来自当前 Train/Val。

## 上下文与信息边界

get_context 一次返回：asset、frequency、panel、target、metric、fields、operators、
expression_rules、evaluation_rules、reference_library。算子含签名、语义、默认值、
作用域、输出、最小窗口、历史需求、版本与别名；成功注册的运行内算子也在目录中。

`expression_rules.timeframes` 按当前字段和频率给出允许的后缀、聚合与窗口单位；
允许 `adj_close` 的 5m 运行会明确展示 `$adj_close@30m` 和 20 根已完成 30m Bar 的例子。
高周期权限仍按基础字段检查，不因后缀扩大字段范围。具体数值语义见 [DSL](dsl.md)。

命名实验版本可固定字段与 builtin 算子集合；查询只返回该运行允许的目录。
包含月频准备字段的版本另在 `expression_rules.prepared_fields` 返回字段描述、准备公式、
计算频率和发布时间，以及“发布到 5m 后普通窗口仍按 5m 计数”的说明。
准备公式中的中间字段不是方法的额外数据权限；版本名、数据路径及切分日期不进入此上下文。

V1 与 V1 Async 的 Explorer、Analyst、Miner 均可使用上述五个公共只读查询工具。
角色交接和有界查询见 [V1](methods/v1.md)，查询不替代候选评估或扩大计划权限。

目标说明包含价格字段、Bar 期限、收益定义与边界，不包含标签数组。日期、fold、universe、
运行／快照 ID 留在平台内部。remaining_attempts 仅在评估结果中出现，不进固定提示词。
平台内部 `get_context()` 仍包含预算，不直接序列化给模型；模型入口是 `query("get_context", {})`。
查询返回独立副本，不能修改平台状态。get_context 不加载参考公式，普通评估扣预算不改变元数据。

`evaluation_rules.clipping` 向所有方法提供统一分位配置与 train 拟合／冻结规则，
不返回因子或标签数组。评分裁剪与缺失语义见[实验规范](research_protocol.md#统一评估与准入)。

```python
# session 由平台绑定，示例只查询已有研究信息
context = session.query("get_context", {})
print(context["fields"], context["operators"])
references = session.query("library_search", {"source": "reference", "limit": 5})
```

## 提交与反馈

模型侧调用示例（具体封装由方法负责）：

```json
{"tool":"evaluate","arguments":{"expression":"TS_MEAN(RETURN($close,1),12)","hypothesis":"短期趋势延续"}}
```

反馈包含 candidate、factor_id、status、metrics、direction、coverage、failure_reason、
diagnostics，以及 accepted、reason、max_abs_corr、nearest_factor_id、comparison_complete、
library_version、factor_trial_index、remaining_attempts。无法计算的值保留 null。
`factor_trial_index` 是本运行的评估尝试序号，`factor_id` 是因子定义身份，
`nearest_factor_id` 是查重得到的最相似库成员身份。尝试序号不按入库或计算成功重新编号。
版本规则 `library_residual = "residual_ic_v1"` 开启时，反馈另含 `residual_ic` 与
`library_r2`：候选 Val 值对当前全库 Val 值做岭投影（只用因子值、不用目标），`library_r2` 是库
解释的方差比例，`residual_ic` 是残差的主指标 Val IC 乘以 √(1−library_r2)，完全重复时连续降为 0。
它只是诊断，不改变准入；未开启的版本不计算，原子记录与反馈也不出现这两个字段。
旧原子记录的 `trial_index`、`nearest_factor` 在既有读取入口解析，不改写历史文件；
新反馈和新记录只写新名称。引用文字 `trial:N` 保持不变。
新运行将原子评估记录保存为 `factor_trials/00000001.json` 等文件；编号字段为
`factor_trial_index`。历史 `trials/` 仍可读取和恢复，旧运行继续写入原目录。
train 为裁剪后的原始方向指标，val 再按训练方向调整；val_raw 为未调整方向的裁剪后指标。

表达式计算成功不等于入库；准入与查重遵循[实验规范](research_protocol.md)。每次实际提交
均计尝试，重复、非法与质量拒绝也计数；只读查询不计数。evaluate 不返回标签或 test 结果。

## 训练相关性

`session.factor_correlations([(factor_id_a, factor_id_b), ...])` 是方法内部公共能力，
不属于上述五个 JSON 只读工具。只接受本运行已评估、具有规范 AST 的因子身份。
返回 FactorCorrelation：left、right、value、n_obs、aggregation、split="train"。

- A 股计算训练期每日横截面 Pearson，再逐日等权；期货合并品种的合约样本计算 Pearson，
  按训练期品种 SQRT 成交额加权。
- 使用共同有限值、既有 eligible、目标有效性和训练 split；每组至少 5 个观测，
  总重叠至少为 `min_corr_overlap`，未知相关不能当零。
- 训练与验证因子值缓存均使用统一 train 边界裁剪后的评分值；缺失缓存重建沿用同一口径。
- 不返回因子数组或标签，不产生新 trial 或准入；耗时计入运行，缓存缺失仅重建请求因子。

任务进程默认缓存 208 个训练值面板，支持约 200 个库成员并为父节点和当前候选留余量。
正式库另在内存中保留最多 200 个成员的验证值，供入库去重复用；两者按最近使用顺序淘汰。
这是缓存容量，不限制正式库的成员数量，也不改变完整时段、有效样本或相关性计算口径。
成员验证值在原子 trial 提交后保留，已有磁盘缓存继续作为恢复来源。每个运行独立持有内存
缓存，进程退出后释放；恢复时先还原成员元数据，首次访问缺失值时按需重建，之后复用。
缓存不进入方法状态或检查点，不新增配置／数据／冻结指纹。内存占用随实际面板行数增长，
容量按因子数量控制，不代表进程总内存上限。

训练值和成员验证值进入内存缓存时按 `row_id` 排序；候选验证值在比较前排序一次。
两份面板的行 ID、类型与顺序一致且没有空行 ID 时，直接配列计算，避免逐对 join。
行集合不同或存在空行 ID 时沿用共同样本 join 和唯一性校验。排序不丢弃缺失因子值，
每对仍按共同有限值计算原指标；Spearman 仍在每对有效交集上重新排名，不抽样或缓存全局排名。

`session.validate_expression(expression)` 仅作编译预检，返回规范 AST 或错误，不读数据、
不消耗评估尝试。内部底层字段／算子查询用于组装 context，不另开放模型工具。
验证见 [test_research_tools.py](../tests/test_research_tools.py)。
