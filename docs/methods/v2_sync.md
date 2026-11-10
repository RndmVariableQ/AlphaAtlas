# V2 Sync：按残差信息搜索新机制

入口 `--method v2_sync`，实现标识 `atlas_mechanism_v2_sync`。目标是用新机制快速扩充因子库：
奖励因子为全库带来的新信息，而不是入库次数。它与 2026-10 已删除的旧 `v2` 方法无关。

## 与 V1 的差异

流程沿用串行 [V1](v1.md)：Explorer 提出二至四个方向并按四项评分选择，Analyst 制定计划，
Miner 逐个提交候选，平台评估、准入，Region 以 Metropolis 退火选择。同 seed 可复现轨迹。
只改变以下几处：

1. **Region 视为机制**：提示词要求说明谁的行为或约束产生可预测性、在现有字段留下的痕迹、
   新因子应与哪些已入库因子或 Region 弱相关，以及几次尝试后什么结果能否定它。跨学科视角
   （信念更新、流动性、套保压力、多尺度、状态空间、价量仓协同等）只是生成新机制的来源，
   标签本身不得分。初始文法 Region 只是测量族，不是机制。
2. **Region 得分**：每次尝试的平均 `residual_ic`（见[研究工具](../research_tools.md)）除以
   `sea_level`；编译失败或无法计算的尝试计 0 但消耗一次尝试。带符号平均让噪声相互抵消，
   不只累计正值。温度、接受概率和每计划候选数沿用 V1。
3. **地图状态**：每个 Region 的 `residual_information` 由原子 trial 每次推导，不另存状态：
   尝试数、平均与最近 `probe_attempts` 次残差、最佳残差，以及状态

   | 状态 | 条件 |
   | --- | --- |
   | probing | 尝试少于 `probe_attempts` |
   | seabed | 已试探，无因子残差达到 `sea_level` |
   | land | 曾有因子残差达到 `sea_level` |
   | saturated | land，且尝试达到 2×`probe_attempts` 后最近 `probe_attempts` 次都低于 `sea_level` |

   seabed 与 saturated 只对已检验的实现成立，可凭新证据回访。
4. **按特征跨度分档**（`scale_statistics`，默认关闭）：同一机制在不同周期上效果可能不同，
   而区域按机制划分、不按周期划分。开启后 `residual_information.by_scale` 把区域尝试按
   特征跨度分为 `up_to_1h`、`1h_to_1d`、`over_1d` 三档，每档给出同样的统计和状态，提示词
   说明某档的饱和或海底不代表其他未测档。特征跨度由候选表达式推导
   （`timeframes.feature_span_minutes`）：因子读取的最早与最新基础 Bar 之间的交易时长，
   各节点窗口按其所在子树的频率计；交易日按 240 分钟近似，例如 5m 上 `RETURN($close,12)`
   为 60 分钟、`TS_MEAN($close@1d,5)` 约 5 天。分档只是统计口径，不改变执行、区域得分、
   Metropolis 或准入；无法解析的表达式只计入区域总数。
5. **只用命名的机制区域**（`mechanism_regions`，默认关闭）：平台为所有方法预置的五个文法区域
   （`baseline_grammar`：return、delta、mean、std、zscore）按算子族划分，不是机制，但模型容易
   把它们当默认标签。开启后它们在 Explorer 地图中标为 `reference_only`，新计划不得归入；
   `new_region` 须带 `name`（不超过六个词的机制名，不得与已有区域重名，重名时应复用其
   `region_id`），完整机制写在 `hypothesis`。名称保存在方法状态的 `proposed_regions` 中，
   用于 Explorer 地图和观测台标签；平台 Region 协议不变。文法区域仍注册，以保留
   `price_volume` 大陆和其他方法的口径。

6. **按状态收束深挖**（`adaptive_depth`、`region_budget_share`，默认关闭）：退火只按预算进度把
   前一半设为广度、后一半设为深度，这不是标准模拟退火的一部分；Luna v3 后 500 次只新开 1 个区域，
   一个已饱和的区域用掉 51.5% 预算。开启 `adaptive_depth` 后，深度轮的活跃区域若已饱和、海底或
   关闭，该轮改为广度并记录 `mode_reason`。`region_budget_share` 给单个区域设预算上限，达到后该
   区域在 Explorer 地图中标为 `closed`，新计划不得归入（复述其假说也不行）。v3 的初始温度 0.25
   沿用 V1 的入库率得分；以海平面为单位的残差得分差更大，较差区域的接受率前期只有约 0.18。

## 配置与版本

方法参数为 V1 参数加 `sea_level`（默认 0.005）、`probe_attempts`（默认 5）与
`scale_statistics`、`mechanism_regions`、`adaptive_depth` 与 `region_budget_share`（默认均关闭，
关闭时配置记录与旧运行一致）。`sea_level`
取自 v5 两次运行 422 个候选的离线残差分布：单候选残差噪声约 ±0.005，约 6.5% 高于 0.005。

`v2_sync` 只能在开启 `library_residual = "residual_ic_v1"` 的规则下运行，并要求共享
ICIR／换手诊断。首个版本 `rq8_zk26_seeded_fold2_v2sync_v6` 与
`rq8_zk26_seeded_fold2_overview_luna_v5` 的数据、34 字段、算子、fold2、B26 初始库和下游
模型相同，只开启残差规则并只配置 `v2_sync`（模型参数取 v5 的 `v1`）。其他方法不在 v6 上
运行，以免反馈中多出残差信息；对照组为 v5 上的 `v1`。

`rq8_fold2_v2sync_haiku_v1` 与 `rq8_fold2_v2sync_haiku_v2` 沿用主表 fold2 设定（Kernel 八字段、
空初始库），模型为 Claude Haiku 5.5；v2 在 v1 上开启 `scale_statistics`，v3 在 v2 上再开启
`mechanism_regions`。

```bash
uv run atlas run --config rq8_zk26_seeded_fold2_v2sync_v6 --method v2_sync --attempts 300 --search-only
uv run atlas run --config rq8_zk26_seeded_fold2_overview_luna_v5 --method v1 --attempts 300 --search-only
```

## V2 Async

入口 `--method v2_async`，实现标识 `atlas_mechanism_v2_async`。它把 V2 Sync 的提示词、区域得分与
证据、分档统计和命名机制区域规则，接到 [V1 Async](v1_async.md) 的并发框架上：Explorer 按空闲槽位
一次提交多个方向，各计划组的 Analyst 与 Miner 并发等待模型，平台仍逐个评估候选，状态只由一个线程
写入。配置为 V2 Sync 参数加 `parallel_plans`；检查点版本独立，不能与 `v2_sync` 或 `v1_async` 互相
续跑。并发会改变计划顺序与退火时机，所以与串行 V2 Sync 的差异要按方法差异解读。

`rq8_fold2_v2sync_luna_v4` 在 `rq8_fold2_v2sync_luna_v3` 上把温度改为 0.45→0.08（按 v3 退火记录中
较差区域得分差约 0.3 标定：起始接受率约 0.5，结束约 0.02），并开启 `adaptive_depth` 与
`region_budget_share=0.15`。

`rq8_fold2_v2async_luna_v3` 与 `rq8_fold2_v2sync_luna_v3` 只差方法（`parallel_plans=5`）。

## 观测台地图

开启残差规则时，准入还记录候选与当时每个库成员的 Val 相关性（`library_similarity`），
初始化时记录初始库成员两两相关性（`run.json` 的 `initial_library.correlations`）。
`atlas ui` 只用这些记录绘制地图，无需重载行情；规则见[开发指南](../development.md#观测台)。

## 限制

- 残差投影只去除线性冗余；非线性重复会被高估，组合增量仍以 `atlas experiment` 的配对
  LightGBM 评估为准。
- 残差依赖评估时的库，先入库者占优；单个候选残差噪声较大，状态按区域汇总判断。
- 每次计算都要把当前全库 Val 值组成矩阵；成员很多时内存和时间随之增加。
- 特征跨度是近似：交易日固定按 240 分钟计，夜盘品种的 5m 长窗口会被偏判为跨日；
  自定义算子不计窗口。
- 新算子开发不在本版范围；缺少能力的方向沿用 V1 的受阻计划处理。

自动验证见 [test_residual.py](../../tests/test_residual.py)：投影数值、重复因子、规则开关与
旧记录格式、状态推导、配置与检查点版本，特征跨度与分档统计、机制区域命名与文法区域限制，以及经 runner 的残差记录、地图输入与中断续跑。
