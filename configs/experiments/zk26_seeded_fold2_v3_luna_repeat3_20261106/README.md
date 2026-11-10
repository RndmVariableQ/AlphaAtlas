# B26 AlphaAtlas V3：Luna 第三次运行

按用户要求，在前两次分别新增 16、5 个成员后，再从 B26 独立运行一次。
运行 ID：`futures_kernel_5m_full-all-fold2-v3_async-42-35fc379af6`。
Fold2、seed 42、300 次新增尝试，保留 200 次前缀；初始库始终为 B26。

搜索沿用 `rq8_zk26_seeded_fold2_v3async_luna_20261106`，源码为
`artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/source/v3/src`。
计划并发 8、评估并发 8、Explorer 3、greedy，每区域最多准入 5 个因子、执行 1 个计划。
模型、提示词、准入门槛与前两次一致；不向新搜索提供此前的新增因子或 Test 结果。

自身搜索冻结后立即拟合和评估，不等待其他实验。下游使用
`rq8_zk26_lgbm_defaults_20261106`：Linear OLS；LightGBM 省略 `min_data_in_leaf`
（原生默认 20）、`lambda_l2=0`，其余参数沿用现有配对评估。
评估源码复制自第二次运行的保存版本；`score.py` 复用第二次运行已核验的数值评分函数。

Val 末固定名单、同权重同样本：Pearson／Spearman IC 均为 73 品种，WMSE 为 74 品种。
ΔIC 相对配对 B26；95% 区间按年分层、全市场同步 20 交易日块、2,000 次重采样，
seed 20261102。Spearman 每次重新排名。总表保留 Luna 原始目标 WMSE，完整结果保存两种目标。

前两次拒绝原因以各自运行报告为准：质量门槛拒绝分别 251、256 次；行为重复 13、12 次；
低覆盖 10、14 次；常量 3、8 次。其余为编译、表达式重复或训练指标未定义。
这是同 seed 的在线模型独立调用，异步执行和模型输出均可能不同；现有两次结果不足以
稳定估计搜索方差。入库数量与组合增量表现分别报告，区块区间不包含搜索随机性。

## 执行与恢复

从仓库根目录执行：

```bash
.venv/bin/python -u configs/experiments/zk26_seeded_fold2_v3_luna_repeat3_20261106/run.py
```

顺序执行搜索、拟合、Test、评分和归档；任一阶段失败立即停止。
使用平台现有 trial、checkpoint 和冻结产物恢复同一 Run ID。
只执行单阶段时，在命令后加 `search`、`fit` 或 `test`。

后台会话：`b26-v3-luna-repeat3-20261106`。
产物与日志：`artifacts/comparisons/zk26_seeded_fold2_v3_luna_repeat3_20261106/`。
完成后归档至 `docs/records/zookeeper26_fold2/v3_luna_repeat3_20261106/`，
并向 `experiment_overview.md` 原有 B26 Luna 表追加一行。
