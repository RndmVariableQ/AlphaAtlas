# B26 AlphaAtlas V3：Haiku 5.5 / Luna 第二次运行

按用户要求，两组同时从 B26 重新搜索，Fold2、seed 42，各 300 次新增尝试，
保留 200 次前缀。原运行与新增因子不导入本轮。

- Haiku 5.5：`futures_kernel_5m_full-all-fold2-v3_async-42-27dcf84074`。
- Luna：`futures_kernel_5m_full-all-fold2-v3_async-42-5602cf6a24`。

搜索沿用各自 `rq8_zk26_seeded_fold2_v3async_{haiku55,luna}_20261106` 版本，
以及 `artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/source/v3/src`
保存的 V3 源码。计划并发 8、评估并发 8、Explorer 3、greedy、区域最多准入 5，
每区域最多执行 1 个计划；均与首轮相同。

各组搜索冻结后可以拟合。按用户要求，Luna 在自身模型保存后先评估并填表，
其 Test 结果不回传 Haiku 搜索；后者完成后补入同一报告。评估使用
`rq8_zk26_lgbm_defaults_20261106`：Linear OLS，LightGBM 省略 `min_data_in_leaf`
（原生默认 20）、`lambda_l2=0`，其余配置不变。评估源码来自默认参数实验的保存版本，
仅将 `evaluation.py` 同步为本轮已验证的 Spearman 区块重采样实现。

复用 Val 末固定名单：Pearson、Spearman IC 为共同 73 品种，WMSE 为 74 品种。
报告相对 B26 的 ΔIC 和 95% 区间，按年分层、全市场同步 20 交易日块、
2,000 次配对重采样、seed 20261102；Spearman 每次重新排名。
完整保存原始和变换后目标 WMSE，总表沿用 Luna 原始目标、Haiku 变换后目标。
这是用户指定的同 seed 历史诊断重复，不能解释为多 seed 稳定性检验。

## 执行与恢复

从仓库根目录执行：

```bash
.venv/bin/python -u configs/experiments/zk26_seeded_fold2_v3_repeat2_20261106/run.py
```

入口同时启动两组搜索，再依次拟合、评估和更新报告；复用平台已有的 trial、
checkpoint、冻结与拟合产物恢复，不创建新的 Run ID。搜索失败或未冻结时不打开 Test。
只运行单阶段可在命令后加 `search|fit|test` 和 `haiku|luna`。
单组已保存 Test 后，可用相同评估源码执行 `score.py --group luna` 或
`score.py --group haiku`，立即归档；随后复用已保存评分，合并另一组结果。
原计划保留启动时的整组流程，实际按独立冻结组评估的节奏以此处和完成记录为准。

后台会话名：`b26-v3-repeat2-20261106`。
历史日志与产物位于 `artifacts/comparisons/zk26_seeded_fold2_v3_repeat2_20261106/`：
`run.log` 为总进度，`haiku-search.log`、`luna-search.log` 为各组搜索进度。
再次执行时子进程日志写入 `logs/experiments/zk26_seeded_fold2_v3_repeat2_20261106/`。
完成后归档至 `docs/records/zookeeper26_fold2/v3_repeat2_20261106/`，并在
`experiment_overview.md` 的 B26 Luna、Haiku 表中分别增加一行。
