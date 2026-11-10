# B26 AlphaAtlas V3 不分档：Luna 第一次运行

按用户要求，从 B26 独立运行 V3 不分档版本（两组之一）。Fold2、seed 42、300 次新增尝试，保留 200 次前缀；
初始库始终为 B26。

搜索版本 `rq8_zk26_seeded_fold2_v3async_luna_noband_20261107`：与前三次 V3
（`rq8_zk26_seeded_fold2_v3async_luna_20261106`）只差 `scale_bands = "none"`，Region 按全部尝试判定
状态，Explorer 提示词不再列回看分档。源码为
`artifacts/comparisons/zk26_seeded_fold2_v3_luna_noband_20261107_source/src`（prompts 工作区快照）。
计划并发 8、评估并发 8、Explorer 3、greedy，每区域最多准入 5 个因子、执行 1 个计划。

下游评估、配对 B26、73／74 品种口径、区间方法与第三次运行相同，评估源码复制自第三次运行，
`score.py` 复用第二次运行已核验的评分函数。

## 执行与恢复

从仓库根目录执行（不设代理）：

```bash
.venv/bin/python -u configs/experiments/zk26_seeded_fold2_v3_luna_noband_r1_20261107/run.py
```

顺序执行搜索、拟合、Test、评分和归档；任一阶段失败立即停止。恢复时重跑同一命令。
产物与日志：`artifacts/comparisons/zk26_seeded_fold2_v3_luna_noband_r1_20261107/`。完成后归档至
`docs/records/zookeeper26_fold2/v3_luna_noband_r1_20261107/`。总表需人工整理。
