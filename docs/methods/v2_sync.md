# V2 Sync：按残差信息搜索新机制

入口 `--method v2_sync`，实现标识 `atlas_mechanism_v2_sync`。目标是用新机制快速扩充因子库：
奖励因子为全库带来的新信息，而不是入库次数。它与 2026-10 已删除的旧 `v2` 方法无关。

## 与 V1 的差异

流程沿用串行 [V1](v1.md)：Explorer 提出二至四个方向并按四项评分选择，Analyst 制定计划，
Miner 逐个提交候选，平台评估、准入，Region 以 Metropolis 退火选择。同 seed 可复现轨迹。
只改变三处：

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

## 配置与版本

方法参数为 V1 参数加 `sea_level`（默认 0.005）与 `probe_attempts`（默认 5）。`sea_level`
取自 v5 两次运行 422 个候选的离线残差分布：单候选残差噪声约 ±0.005，约 6.5% 高于 0.005。

`v2_sync` 只能在开启 `library_residual = "residual_ic_v1"` 的规则下运行，并要求共享
ICIR／换手诊断。首个版本 `rq8_zk26_seeded_fold2_v2sync_v6` 与
`rq8_zk26_seeded_fold2_overview_luna_v5` 的数据、34 字段、算子、fold2、B26 初始库和下游
模型相同，只开启残差规则并只配置 `v2_sync`（模型参数取 v5 的 `v1`）。其他方法不在 v6 上
运行，以免反馈中多出残差信息；对照组为 v5 上的 `v1`。

```bash
uv run atlas run --version rq8_zk26_seeded_fold2_v2sync_v6 --method v2_sync --attempts 300 --search-only
uv run atlas run --version rq8_zk26_seeded_fold2_overview_luna_v5 --method v1 --attempts 300 --search-only
```

## 限制

- 残差投影只去除线性冗余；非线性重复会被高估，组合增量仍以 `atlas experiment` 的配对
  LightGBM 评估为准。
- 残差依赖评估时的库，先入库者占优；单个候选残差噪声较大，状态按区域汇总判断。
- 每次计算都要把当前全库 Val 值组成矩阵；成员很多时内存和时间随之增加。
- 新算子开发不在本版范围；缺少能力的方向沿用 V1 的受阻计划处理。

自动验证见 [test_residual.py](../../tests/test_residual.py)：投影数值、重复因子、规则开关与
旧记录格式、状态推导、配置与检查点版本，以及经 runner 的残差记录、地图输入与中断续跑。
