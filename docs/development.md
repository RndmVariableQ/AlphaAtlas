# 开发指南

## 原则

- 先明确需要解决的问题与可验收结果，再修改代码；优先复用现有接口和提交路径。
- 用最少的模块、依赖和状态实现当前需求。相关小功能集中放置，单一调用流程不建立通用框架。
- 数据、算法与证据各有归属；缺失和失败显式报告，不用默认分数、伪数据或隐藏回退掩盖。
- 保留已有因子／算子身份、配置和冻结校验；源码指纹仅记录来源。不增加重复哈希体系、
  检查点校验和、成员缓存指纹、额外账本、任务队列或兼容迁移层。
- 恢复依赖原子 trial 与方法状态，缓存按需重建；不以全文件扫描或强制重算替代冗余机制。
- 修改同步更新对应文档，删除被替代代码和依赖；保留原数据与历史运行产物。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| [contracts.py](../src/alpha_atlas/contracts.py) | 公共对象与方法协议 |
| [assets/](../src/alpha_atlas/assets/) | 数据采集、导出、审计与 MarketData |
| [expressions.py](../src/alpha_atlas/expressions.py)、[operators/](../src/alpha_atlas/operators/) | DSL、算子、编译与数值执行 |
| [evaluation.py](../src/alpha_atlas/evaluation.py) | 独立标签、split、指标与诊断 |
| [library.py](../src/alpha_atlas/library.py)、[atlas.py](../src/alpha_atlas/atlas.py) | 正式准入、去重、区域证据 |
| [session.py](../src/alpha_atlas/session.py) | 公共研究工具、候选评估与预算 |
| [methods/](../src/alpha_atlas/methods/) | 方法搜索机制、奖励、提示词及状态 |
| [runner.py](../src/alpha_atlas/runner.py)、[checkpoint.py](../src/alpha_atlas/checkpoint.py) | 运行、恢复、冻结与 OOS |
| [storage.py](../src/alpha_atlas/storage.py)、[reporting.py](../src/alpha_atlas/reporting.py) | 原子记录、只读重建与报告 |
| [terminal.py](../src/alpha_atlas/terminal.py) | 终端进度与流式状态，各层共用，不读写记录 |
| [dashboard/](../src/alpha_atlas/dashboard/) | 本地观测台：运行监控、研究记录与残差地图；唯一写操作是停止运行（向持有运行锁的进程发 SIGINT，由运行自行记为 interrupted） |
| [cli.py](../src/alpha_atlas/cli.py) | 命令行入口 |

```text
方法生成候选 → runner / SearchSession → 编译与评估 → 因子库准入 → 原子 trial → 方法反馈
                                           ↑
                                  MarketData（特征与目标分离）
搜索结束 → 冻结定义、方向与依赖 → 独立 OOS → 报告
```

方法决定搜索机制和奖励组合，公共平台决定数据权限、数值、准入、预算和冻结。
具体公共对象以代码定义为准，不另维护重复字段表。

## 开发流程

字段命名使用有明确含义的领域名：编号用 `_index`，身份用 `_id`，证据引用用 `_ref(s)`，
数量优先用 `_count`。同一含义在契约、方法、持久化和模型反馈中保持一致；已有明确语境的
字段不机械增加前缀。因子评估尝试编号为 `factor_trial_index`，一次研究计划执行编号为
`research_plan_index`；编号仅在当前运行内有效，不能代替因子身份或计划完成数量。
新运行的因子评估记录统一写入 `factor_trials/`，所有方法共用同一读写路径。
读取、报告和恢复兼容历史 `trials/`；旧运行续写原目录，不搬移或拆分历史记录。
同时存在两个目录时明确报错，不合并两套记录。

1. 确认修改属于哪一层，阅读该层文档；方法接入遵循[复现规范](method_reproduction.md)。
2. 选择最小改动。新增字段先进入适配层；新增指标进入公共评估层；方法不另建数据或工具入口。
3. 用合成数据覆盖相关数值、边界、失败和回归；预期值应有独立数值依据。
4. 执行检查并更新所属文档。真实采集、模型调用和市场实验作为独立操作记录。

```powershell
uv sync
pwsh -File scripts/check.ps1
```

使用 Python 3.13、Polars、100 字符行宽，依赖由 `uv.lock` 固定。检查依次运行 Ruff、
格式检查、算子目录一致性与 pytest；测试不访问真实凭据、供应商或模型服务。
算子目录由 `scripts/operator_catalog.py` 生成，不手工编辑其内容。

新算子及复杂内核示例见 [DSL](dsl.md)，公共工具示例见[研究工具](research_tools.md)。
本地 group_batch 注册通过子进程验证后使用 Numba 执行；正式执行无硬性内存隔离，
不承诺在内核运行中立即响应中断。缓存额度也不等于进程总内存上限。

## 终端与日志

终端展示统一由 `terminal.py` 处理，按运行信息、Agent 阶段、候选评估组织。运行信息只在启动、
恢复和结束时给出；研究日志以 `[计划 #编号 · Explorer/Analyst/Miner]` 为标题，每次实际
请求、方向提交和计划提交都作为持久事件保留。方向和假设完整换行，不截成固定长度摘要。
并行计划按实际发生顺序交错展示，通过计划编号关联，不伪装成串行流程。
异步取回回复不重复记录请求或研究方向。
MCTS-LLM 按 `[MCTS-LLM · 候选 #编号]` 保留生成建议、生成公式、风险判断的阶段请求及正式
结果，查询和修正另列事件；编号来自当前评估进度。正式建议和风险依据属于模型返回结果，
流式推理正文仍隐藏。恢复可展示已保存的阶段结果，不重新请求已保存的回复。

`terminal_progress` 默认记录关键事件，标题独立成行，字段缩进；路径、原因和提醒按终端宽度
换行，省略无值字段。Agent 对话请求使用持久事件；`transient=True` 用于等待、评估等即时进度：
交互终端原地更新
一行，后续事件先清除该行；重定向时每次实际请求记录一条完整进度，不写动画、回复片段或
重复的请求完成行。重试、失败和中断始终作为事件保留，流式回复仅在交互终端预览，隐藏推理。
窄终端优先保留阶段、计划和对话进度，空间不足时省略回复预览。
流式状态按实际收到的内容区分「等待首字」「思考中」「接收回复」，只显示推理阶段状态，
不展示推理内容。收到内容后连续 30 秒没有新片段时显示「无新内容 Ns」；计时和动画本身
不代表服务端仍在生成。重试清空本次回复状态，超时仍沿用运行保存的配置。
异步等待不启用回复动画，也不作为请求失败显示。
Explorer 的收敛提醒在第 21 次对话前独立记录一次，后续请求不重复刷提醒。

候选结果以 `[计划 #编号 · 评估]` 为标题，缩进展示名称、方向、覆盖、train/val 指标、完整
公式、拒绝原因和耗时。runner 从方法现有的待评估计划读取编号，仅传给展示层；不复制计划
状态、不写入额外元数据、不按候选名称或输出顺序推测归属。恢复沿用同一来源。
没有研究计划的搜索方法不添加计划或 Agent 标记。原始 Train 与定向 Val 在表头说明，
指标只列一次，不在结果摘要中再列四位小数版本。
候选与 OOS 进度日志中的期货 IC 只展示 √成交额加权 Pearson 和 Spearman；完整评估数据及
正式报告仍保留品种等权指标，A 股沿用原有指标。
展示只读取已有结果，不计算指标或裁决状态。进度写入 stderr，stdout 保留命令结果；
`--quiet` 关闭全部进度。运行产物仍是研究证据和恢复的权威来源。

新运行的 CLI 关键进度事件同时保存到 `logs/cli/`，`--quiet` 不创建进度日志；实验编排脚本的
子进程日志保存到 `logs/experiments/<配置目录>/`。异常堆栈和命令结果仍分别输出到 stderr、
stdout；需要完整转录时将输出重定向到 `logs/`。历史日志保留原位置，以维持实验记录链接。

## 观测台

`atlas ui` 启动仅监听本机的只读 HTTP 服务（标准库，无前端依赖），扫描 `artifacts/runs`、
`artifacts/comparisons/*/workspace/artifacts/runs` 及 `--runs` 指定目录。接口只读取 run.json、
检查点与原子 trial，不写文件、不加运行锁、不重算因子；运行 ID 是不透明键，不接受路径。

- **监控**：状态、进度、入库、入库因子残差（入库因子 residual_ic 之和）、评估结果分布、当前计划、模型用量与速度。
  `running` 超过 30 分钟无新记录显示为疑似停滞，仅作提示。
- **研究记录**：按 `research_plan_index` 展示 Explorer 提案与评分、Analyst 计划、Miner 候选及
  平台评估、退火裁决和受阻计划；所有方法的研究计划记录均可查看。
- **地图**：仅开启 `library_residual` 的运行可用。每个 Region 的尝试组成一座岛或海台，
  区域间按已记录的成员相关性布局；缺少相关性证据时距离不代表相关性。海拔按残差与海平面
  计算，初始库成员视为已知陆地；区域状态调用对应 V2／V3 方法的状态函数。噪声、光照和云雾
  只是渲染纹理，时间轴按 trial 编号回放。
- 链接参数 `#run=<id>&tab=map|plans|monitor&plan=<n>|trial=<n>` 可直接打开指定视图。

## 文档维护

| 位置 | 唯一职责 |
| --- | --- |
| [README](../README.md) | 项目介绍、安装、最小运行与文档导航 |
| [design.md](design.md) | 研究原理、未实现设想与后续验收目标 |
| [research_protocol.md](research_protocol.md) | 数据、评价、共享诊断与实验流程 |
| [method_reproduction.md](method_reproduction.md) | 所有方法的接入、公平比较与恢复规范 |
| [dsl.md](dsl.md)、[research_tools.md](research_tools.md)、[model.md](model.md) | 可直接使用的接口与数值约定 |
| [methods/](methods/README.md) | 方法状态，以及各方法特有机制和偏离 |
| [records/](records/experiments.md) | 带日期、运行 ID 和适用范围的历史证据 |

同一规则只在所属文档定义，其他位置链接；配置默认值以配置文件为准，记录采用运行保存值。
当前状态与历史记录分开，历史测试数量不代表当前通过情况。更新说明直接描述最终行为，
不追加聊天过程、施工流水账或其他项目的部署内容。纯介绍、日报和计划不另建重复入口。
新增文档须对应独立职责，并检查本地链接及锚点；图示不是实现状态的依据。
