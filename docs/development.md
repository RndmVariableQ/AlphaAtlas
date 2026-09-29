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
