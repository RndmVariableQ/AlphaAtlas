# AlphaAtlas 项目开发约定

本文件汇总开发任务必须遵守的边界；详细规范见 `docs/`，接口和默认值以代码、配置为准。

## 1. 项目架构与职责

AlphaAtlas 是 train/val 因子搜索与研究评估平台。方法负责搜索与奖励；平台负责数据、执行、评估、准入、
预算、冻结、恢复及 OOS。

```text
数据适配 → MarketData → 方法生成候选 → runner / SearchSession → 编译与评估
                                                        → 因子库准入 → 原子 trial → 方法反馈
搜索结束 → 冻结定义、方向与依赖 → 独立 OOS → 报告
```

| 模块 | 职责 |
| --- | --- |
| `assets/` | 供应商数据采集、导出、审计及 MarketData 构建 |
| `expressions.py`、`operators/` | 表达式 DSL、算子、编译与数值执行 |
| `evaluation.py` | 特征与目标分离、数据划分、指标及诊断 |
| `library.py`、`atlas.py` | 正式准入、去重与区域证据 |
| `session.py` | 公共研究工具、候选评估与预算边界 |
| `methods/` | 方法专属搜索策略、奖励、提示词与状态 |
| `runner.py`、`checkpoint.py` | 运行调度、恢复、冻结及 OOS |
| `storage.py`、`reporting.py` | 原子持久化、只读重建与报告 |
| `cli.py` | 命令行入口 |

公共协议以 `contracts.py` 和现有代码为准。方法共用 SearchSession 的 `get_context`、`library_search`、
`library_get`、`library_list`、`library_stats`，并由 runner 提交 `evaluate`；不另建工具或数据权限。

## 2. 必读规范与维护位置

先读[开发指南](docs/development.md)，再按任务阅读：

| 主题 | 权威文档 |
| --- | --- |
| 数据来源、PIT、fold、评价、诊断、实验和 OOS | [研究协议](docs/research_protocol.md) |
| 方法接入、来源归属、公平比较、预算与恢复 | [方法复现规范](docs/method_reproduction.md) |
| DSL 与算子数值语义 | [DSL](docs/dsl.md) |
| SearchSession 公共工具 | [研究工具](docs/research_tools.md) |
| 方法状态与具体机制 | [方法目录](docs/methods/README.md) 及对应方法说明 |
| 历史实验事实 | [实验记录](docs/records/experiments.md) |

同步更新所属文档；新增方法更新方法目录，实验结论写入实验记录。配置和历史运行值分别以配置文件、运行产物为准。

## 3. 数据、实验与信息边界

- train/val 仅用于开发反馈；全部开发决策冻结后才评估 OOS。方法不得接收 test 标签或绕过平台读取目标、供应商数据。
- 特征与目标分离，研究只通过 `MarketData` 访问。供应商 API／Parquet 仅限资产适配与导出层；`.parq` 存于
  `data/` 并排除 Git。采集显式发起，manifest 如实记录来源、覆盖和缺失。
- 数据与其他工作区隔离，不改仓库外数据或数据库。期货使用用户指定的 RiceQuant 原生 5m，不重采样或合成连续价。
- 资产身份为 `(exchange, instrument_id)`；期货窗口和目标不跨真实合约或连续段，`product` 仅用于聚合，映射代码留在适配层。
- A 股使用 PIT 逐日成员；1800 是 HS300、ZZ500、ZZ1000 逐日并集。基本面保留公告／生效日期和修订来源，披露修订限制，不造缺失数据。
- 每个 `(asset, dataset, universe, fold, method, seed, run_id)` 独立维护因子库与地图。
- 不记录凭据；测试只用合成数据与模拟服务，不访问真实凭据、供应商或模型服务。项目不提供交易／下单 API。

## 4. Agent 与方法的执行约束

- context 一次提供资产、频率、目标、指标、字段、算子、表达式／评估规则及参考库设置；日期、fold、运行／快照 ID、
  universe 不进入模型 context 或提示词。`remaining_attempts` 只放评估结果，内部预算用于停止控制。
- 参考库由 `benchmark.toml` 统一控制并纳入冻结校验。窗口不设上限，但须为正整数并满足算子最小窗口、完整历史和合约隔离。
- 平台统一数值、准入、预算和冻结，方法负责搜索与奖励。说明论文、作者代码、第三方参考、本地适配和基线的差异；
  不把协议或符号原型称为 LLM-MCTS／AGI。

## 5. 实现、恢复与验证

- 使用 Python 3.13、Polars、100 字符行宽及 `uv.lock` 固定依赖；完成修改后运行 `pwsh -File scripts/check.ps1`。
- 优先复用接口并做最小改动。新机制须对应明确需求或可复现问题；不引入单点抽象、队列、事件系统、通用幂等层、额外账本或迁移层。
- 保留正确性、性能、配置、因子／算子身份和冻结校验。源码指纹只记来源；不新增检查点、算子记录、成员缓存或方法源码哈希。
- 恢复复用原子 trial 和方法状态，缓存按需重建；不全量扫描、重复校验或强制重算。
- 测试覆盖相关数值、边界、失败和回归，不为测试扩建机制。删除替代代码与依赖，保留旧产物；可选增强留待后续。




## AlphaAtlas Agent 设计原则

### 1. 极简主义

只保留当前需求真正需要的概念、工具和状态。

落地：

- 能由服务端完成的，不交给 Agent；
- 能由已有对象表达的，不新增对象；
- 不保留重复工具、重复状态和重复提示词；
- 不为假设中的扩展预设接口。

### 2. 职责正交

每个 Agent 只负责一种类型的判断，不重复承担其他层级的职责。

落地：

- Atlas Explorer Agent：发现未知，提出研究方向；
- Research Analyst Agent：把方向变成研究计划；
- Factor Miner Agent：执行计划中的因子 R&D；
- pipeline 和服务端：负责顺序、评估、裁决和持久化。

### 3. 自由度分层递减

越接近研究问题，自由度越大；越接近具体执行，约束越明确。

```text
研究问题
→ 研究区域
→ 研究方向
→ 研究计划
→ 因子实现
→ 服务端评估
```

下层只能在上层定义的范围内工作，不能扩大范围、改变目标或替换研究方向。

### 4. 选择上移，执行下沉

不确定的选择由上层 Agent 完成；确定、重复、可验证的工作由系统执行。

落地：

- Explorer 选择研究什么；
- Research Analyst Agent 选择如何验证；
- Miner 选择具体表达式；
- 服务端负责数据读取、评估、门禁和状态转换；
- Agent 不自行裁决最终结果。

### 5. 证据优先，结论后置

任何研究判断都必须建立在实际证据上。

落地：

- 先读取和评估，再形成判断；
- 区分假说、Train 初筛、Valid 验证和已入库；
- 证据不足时保留不确定性；
- 不把单一 Train 指标最大化当作成功；
- 不把相关性解释为因果。

### 6. 一个事实，一个权威来源

同一个事实只在一个地方产生和裁决。

落地：

- 研究区域以 Atlas 的 `Region` 为权威；
- `Proposal.region_ref` 表示方向所属区域；
- `Exploration.region_ref` 表示研究任务所属区域；
- 研究计划以 Research Analyst Agent 输出为准；
- 评估和 `qualified` 状态以服务端为准；
- UI 只展示后端状态，不自行维护业务事实。

概念区分：

```text
Atlas Explorer Agent 是角色；
Region 是研究区域对象；
Exploration 是一次研究任务。
```

### 7. 固定主流程，允许局部探索

整体流程固定，Agent 只在自己的层级内探索。

```text
Atlas Explorer Agent 提出方向
→ Research Analyst Agent 确定区域和计划
→ Factor Miner Agent 执行因子 R&D
→ 服务端评估和裁决
→ pipeline 保存研究结果
```

Factor Miner Agent 不读取或写入 Atlas，不改变研究区域或方向，不修改分类目录，
也不直接宣布候选合格。
