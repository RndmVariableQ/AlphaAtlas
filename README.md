# Alpha Atlas

**A Quantitative Knowledge Map for Navigating and Expanding the Frontiers of Alpha Discovery**

**面向 Alpha 发现的量化知识地图：导航、探索与拓展知识前沿**

Alpha Atlas 的出发点是一个简单的想法：因子发现应当积累对市场的理解，让每一次实验都能
帮助研究者判断下一步值得探索什么。

## 项目动机

遗传编程、强化学习和 LLM Agent 等方法，让自动生成和评估因子表达式变得越来越容易。
但候选公式数量的增长，并不必然带来新知识。搜索可能反复发现相似信号，在已有方向上
不断微调，而一些有价值的市场假说仍未得到检验。

因此，因子研究面临的问题不仅是如何提高搜索效率，还包括：**哪些方向已经探索过？
不同因子之间有什么联系？下一步应该去哪里寻找？**

Alpha Atlas 希望通过一张持续演化的量化知识地图，组织已有研究，并为后续探索提供依据。

## 核心设想

在这张地图中，每个因子都由三个相互关联的维度描述：

- **语义：** 因子试图刻画的市场机制或研究假说，例如短期过度反应、流动性恢复和信息传播延迟。
- **计算结构：** 将假说转化为可检验表达式的计算过程，包括所用字段、算子及其组合关系。
- **实证行为：** 因子在数据中的预测能力、换手率、衰减特征、稳定性，以及与其他因子的关系。

这三个维度共同构成因子的完整研究表示。相似的公式可能表现不同，不同的假说也可能产生
高度重合的信号。连接这些信息，有助于识别重复探索、理解因子之间的差异，并发现尚未
充分研究的方向。

## 从因子搜索到知识探索

Alpha Atlas 设想将**预测质量、新颖性与探索覆盖度**共同纳入搜索目标，让系统既关注
有效信号，也关注实验是否带来了新的认识。

知识地图将帮助研究者和搜索算法定位已有成果、寻找相关证据，并选择值得检验的新方向。
地图中的空白只代表探索不足，是否存在有效信号，仍需通过实验判断。

长期来看，我们希望形成一个持续迭代的研究循环：

> 提出市场机制假说 → 在知识地图中定位 → 转化为因子表达式 → 实验验证 → 更新地图 → 选择下一步探索方向

AI 在其中的作用，是结合已有知识提出假说、设计实验并解释结果。探索既可以发生在公式
结构层面，也可以围绕市场机制和研究问题展开；成功与失败的实验，都应为后续研究提供信息。

## 项目愿景与当前阶段

Alpha Atlas 的定位是 **AI 辅助的开放式金融发现（Open-ended Financial Discovery）**。
我们希望让因子研究成为一个持续积累知识的过程：每一次实验，都让我们更清楚地知道
已经理解了什么、哪些问题仍不确定，以及下一步值得探索哪里。

上述内容是项目的研究方向与设计愿景。目前以固定 train/val 区间内的因子搜索为基础，
已提供统一数据接口、表达式 DSL、数值评估、因子库、运行恢复与冻结后的样本外评估。
搜索方法包括 Random、GP、符号 MCTS、区域 UCB 原型，以及 AlphaPROBE、MCTS-LLM、
三 Agent 区域退火搜索和 ReAct 的方法接入。三模态学习地图与自动开放式假说生成仍属后续研究，方法接入也不代表
已复现原论文收益。具体边界见[方法目录](docs/methods/README.md)与[知识地图设计](docs/design.md)。

## 项目安装

需要 **Python 3.13** 和 **uv**。以下命令在仓库根目录执行，示例使用 PowerShell：

```powershell
uv sync
uv run atlas --help
uv run atlas config
```

`uv sync` 安装项目与开发依赖；依赖版本由 `uv.lock` 固定。如需自行获取 RiceQuant 数据，
额外安装供应商依赖：

```powershell
uv sync --extra ricequant
```

LLM 方法还需要配置模型服务。使用前参阅各方法说明：
[AlphaPROBE](docs/methods/alphaprobe.md)、[AlphaSchema](docs/methods/alphaschema.md)、
[MCTS-LLM](docs/methods/mcts_llm.md)、
[三 Agent 区域退火搜索](docs/methods/annealing.md)、
[V1 Explorer 更新版](docs/methods/v1.md)、[V1 异步版](docs/methods/v1_async.md)、[V2 Sync](docs/methods/v2_sync.md)、[ReAct](docs/methods/react.md)。
下方最小运行示例使用无需模型服务的 Random 基线。

## 数据准备与校验

数据独立保存在 `data/`，不随 Git 分发。当前研究支持 RiceQuant 原生 A 股日线和期货
5 分钟数据；A 股股票池使用历史 HS300、ZZ500、ZZ1000 的逐日成员，期货使用真实合约，
窗口和标签不跨合约。

先查看本地数据状态：

```powershell
uv run atlas data-status
```

尚未获取数据时，配置供应商凭据后显式运行采集脚本。将示例路径替换为仓库外已有的
凭据文件路径，不将凭据复制进项目：

```powershell
uv run python scripts/acquire_data.py futures --env-file C:\path\to\credentials.env
uv run python scripts/acquire_data.py ashare --env-file C:\path\to\credentials.env
```

已有数据后，执行对应资产的完整性审计：

```powershell
uv run atlas audit-data --asset futures
uv run atlas audit-data --asset ashare
```

`data-status` 读取采集状态；`audit-data` 检查分区完整性、主键、行情异常等，并保存
`audit.json`。结合 `data/<asset>/manifest.json` 查看实际覆盖、缺失与来源；结构审计
不能替代全面的金融数据质量审查，未完成的数据集默认不能用于正式实验。
数据口径与已知例外见[研究协议](docs/research_protocol.md)和[数据验收记录](docs/records/data.md)。

## 架构

依赖只向下：上层读取下层的结果，下层不感知上层。

```text
结论层  runs/<id>/report.md → experiment_overview.md → docs/records/
实验层  runner（单次运行） · experiment（单次收尾） · incremental（多运行配对比较）
方法层  methods/*：只负责生成候选与奖励，经 SearchSession 访问公共工具
平台层  DSL／算子 · 评估 · 因子库准入 · 预算 · 原子 trial 与恢复
数据层  assets/ → MarketData · configs/versions/ 冻结数据版本 · 参考因子库
```

| 层 | 位置 | 权威内容 |
| --- | --- | --- |
| 数据 | [assets/](src/alpha_atlas/assets/)、[configs/versions/](configs/versions/) | 快照、字段、fold、算子、评价规则与模型配置；冻结后不改，变更新建版本 |
| 平台 | [expressions.py](src/alpha_atlas/expressions.py)、[evaluation.py](src/alpha_atlas/evaluation.py)、[library.py](src/alpha_atlas/library.py)、[session.py](src/alpha_atlas/session.py) | 数值、准入、预算与冻结，所有方法共用 |
| 方法 | [methods/](src/alpha_atlas/methods/) | 搜索策略、奖励、提示词与方法状态 |
| 实验 | [runner.py](src/alpha_atlas/runner.py)、[experiment.py](src/alpha_atlas/experiment.py)、[incremental.py](src/alpha_atlas/incremental.py)、[configs/experiments/](configs/experiments/) | 单次运行、冻结后 OOS／模型，以及预先声明的多方法比较 |
| 结论 | 运行目录 `report.md`、[experiment_overview.md](experiment_overview.md)、[docs/records/](docs/records/experiments.md) | 自动报告与结果总表；结论和限制写入实验记录 |

一次研究的顺序：选定数据版本 → 在 `configs/experiments/` 写下问题、方法、预算与指标 →
搜索并冻结 → 全部开发决策固定后打开 Test → 自动填写总表，人工在实验记录中写结论。
train/val 用于开发反馈；test 不参与搜索、入库或因子选择。

## 实验入口

以下命令在仓库根目录执行。数据不随 Git 分发，缺少或不匹配的快照会报错。

| 用途 | 命令 |
| --- | --- |
| 查看数据状态／审计 | `uv run atlas data-status`、`uv run atlas audit-data --asset futures` |
| 采集数据（显式发起） | `uv run python scripts/acquire_data.py futures --env-file <凭据文件>` |
| 列出／查看冻结版本 | `uv run atlas versions`、`uv run atlas versions rq8_zk26_fold2_v2` |
| 单次运行（含收尾） | `uv run atlas run --version <版本> --method <方法> --attempts 300` |
| 只搜索并冻结 | 上述命令加 `--search-only` |
| 中断后续跑 | `uv run atlas resume artifacts/runs/<run_id>`（可加 `--search-only`） |
| 冻结后收尾 | `uv run atlas finish artifacts/runs/<run_id>`：单因子 OOS、Linear／LightGBM、库内相似度，并填总表 |
| 单项评估 | `uv run atlas test <run_dir>`、`uv run atlas model <run_dir> [--test]` |
| 重建／查看报告 | `uv run atlas report <run_dir>`、`uv run atlas report <run_dir> --oos` |
| 多方法配对比较 | `uv run atlas experiment configs/experiments/<plan>/<scope>.json` |
| 比较只搜索／打开 Test | 上述命令加 `--search-only`；全部 scope 拟合完成后加 `--test` |
| 观测台（监控、地图回放、研究记录） | `uv run atlas ui`，浏览器打开 http://127.0.0.1:8780/ |
| 参考因子库 | `uv run python scripts/evaluate_zookeeper.py --fold fold2`、`uv run python scripts/evaluate_reference.py --fold fold1 --bars-per-day 48` |

方法名见[方法目录](docs/methods/README.md)；`random`、`gp`、`mcts`、`atlas` 无需模型服务。
`atlas experiment` 的 scope 列出 `runs` 时复用已冻结运行，否则按方法 × seed 启动搜索；
运行 ID 由 scope、方法和 seed 确定，重复同一命令即续跑。默认输出到
`artifacts/experiments/<scope.id>/`，历史结果用 `--output` 指定原目录。

最小示例（无需模型服务）：

```bash
uv run atlas run --version rq8_zk26_fold2_v2 --method random --attempts 10
```

运行结果保存在 `artifacts/runs/<run_id>/`，阅读入口为其中的 `report.md`。已保存的模型与
评估结果会复用；失败不填表，同一运行不会重复新增行。正常收尾不请求模型服务。
旧运行沿用 `--asset <资产> --fold <fold>` 入口恢复与评估；新实验使用 `--version`。
`--fields` 只用于显式缩小字段；身份、成员、质量、复权元数据与标签不属于默认特征。
开发与检查要求见[开发指南](docs/development.md)。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [知识地图设计](docs/design.md) | 原理、研究设想与后续验收目标 |
| [开发指南](docs/development.md) | 开发原则、模块职责、检查与文档维护 |
| [Zookeeper 26/37](src/alpha_atlas/factor_libraries/zookeeper/README.md) | JSONL 定义、固定模型 baseline、RiceQuant 代理适配与 PIT 保证范围 |
| [数据与实验规范](docs/research_protocol.md) | 数据边界、评价口径与实验流程 |
| [方法接入规范](docs/method_reproduction.md) | 复现边界、预算、公平比较与恢复 |
| [DSL](docs/dsl.md) / [算子目录](docs/operators.md) | 表达式、数值语义与算子开发 |
| [研究工具](docs/research_tools.md) / [模型评估](docs/model.md) | 公共 session 与下游预测接口 |
| [搜索方法](docs/methods/README.md) | 方法状态、算法、差异及配置 |
| [实验记录](docs/records/experiments.md) / [数据记录](docs/records/data.md) | 历史证据与已知限制 |
