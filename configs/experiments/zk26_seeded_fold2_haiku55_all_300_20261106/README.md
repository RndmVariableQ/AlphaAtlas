# Haiku 5.5：五方法 B26 初始库增量补充

AlphaPROBE、AlphaSchema、MCTS-LLM、ReAct、AlphaAtlas V3 各运行一次，五个搜索并行。
统一使用 `claude-haiku-5-5`，Fold2、seed 42、300 次新增尝试；初始 B26、34 个字段、
200／300 次前缀、准入规则和组合评估沿用原 B26 实验。初始化 26 项不计入新增预算。

四个对照方法的配置来源是 `rq8_zk26_seeded_fold2_overview_luna_v5`，
V3 的配置来源是 `rq8_zk26_seeded_fold2_v3async_luna_20261106`。各自只修改 Chat
模型、地址和凭据环境变量名；AlphaPROBE 的 embedding 保持 Qwen3-Embedding-0.6B。
V3 保留已有库残差反馈、8 个并行计划、8 路评估、3 个 Explorer、每 Region 入库上限 5、
并发计划上限 1、`selection_policy=greedy`。其余方法不额外开启残差反馈。

两个 scope 保留各自的冻结版本与配置，模型参数和 B26 定义一致：

- [methods.json](methods.json)：AlphaPROBE、AlphaSchema、MCTS-LLM、ReAct。
- [v3.json](v3.json)：AlphaAtlas V3。

执行源码保存于本轮 `artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/source/`。
`methods/` 复制本次启动前的主仓库源码；`v3/` 逐字节复用既有 B26 Luna V3 实验的源码。
`react/` 在 `methods/` 副本上补齐初始库及发布特征元数据的提示词展示，修复 B26 初始化后、
首次模型调用前的 `KeyError`；恢复沿用原 Run ID、26 项初始成员和完整 300 次预算。
不修改旧版本、旧运行或其他工作区。运行继续使用原有源码来源记录及冻结校验。

从仓库根目录启动或恢复整轮：

```bash
.venv/bin/python -u configs/experiments/zk26_seeded_fold2_haiku55_all_300_20261106/run.py
```

入口自动读取本仓库 `.env`，不输出凭据。真实模型调用使用现有本机 Claude CLI 桥接
（8765）与 embedding 服务（8003）。桥接不映射 temperature 和输出 token 上限，
沿用既有 Haiku 后端行为。详细边界见 [桥接说明](../../../integrations/claude_cli/README.md)。

原整轮入口等待五个搜索全部冻结后，依次拟合两个 scope 的全部 B26 与 B26＋新增成员模型，
全部模型保存后才读取历史 Test。Linear OLS；LightGBM 50 轮、leaf2000/depth5；
固定 B26 可用的共同训练行和预测行。95% 区间使用按年分层、全市场同步的
20 交易日块、2,000 次重采样。保留所有准入成员，不计算并集、不按 Test 筛选。
协议见 [methods_plan.json](methods_plan.json) 和 [v3_plan.json](v3_plan.json)。

历史日志位于本轮产物目录的 `<method>.log`；再次执行时子进程日志写入
`logs/experiments/zk26_seeded_fold2_haiku55_all_300_20261106/`，组合结果仍保存在 `methods/`、`v3/`。
搜索出错或被停止时，其他搜索继续，但不进入配对评估；重复启动命令可沿原检查点恢复。
进度以原 `artifacts/runs/<run_id>/run.json`、检查点及原子 trial 为准。
2024–2025 已被查看，最终结果属于历史诊断留出集增量。

2026-11-06 按用户后续指示，先评估已冻结的 AlphaPROBE 和 AlphaAtlas V3。
AlphaPROBE 使用 [alphaprobe_ready.json](alphaprobe_ready.json) 显式指向原运行，
输出单独保存在 `alphaprobe/`；V3 复用 `v3.json` 和 `v3/`。搜索、200／300 次前缀、
B26、组合模型和区间配置均保持原值。先并行拟合这两个 scope，全部保存后才并行评估
历史 Test；其他方法的搜索仍独立进行，评估结果不进入其模型上下文或反馈。

本次提前评估的复跑入口与日志位于本轮产物目录：

```bash
.venv/bin/python -u artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/evaluate_ready.py
```

总日志为 `ready-evaluation.log`，阶段日志为 `alphaprobe-fit.log`、`alphaprobe-test.log`、
`v3-fit.log`、`v3-test.log`。该入口只复用上述两个已冻结运行，不依赖 ReAct 的恢复流程。
两组于 2026-11-06 15:26（北京时间）完成，分别保存 `fit.json`、`test.json`、
`results.jsonl` 和 `factors.jsonl`；200／300 次的全部结果见
[实验记录](../../../docs/records/experiments.md#2026-11-06-haiku-55-b26alphaprobe-与-v3-提前配对评估)。

AlphaSchema 冻结后按用户要求单独补充评估，使用
[alphaschema_ready.json](alphaschema_ready.json) 复用原运行，结果写入 `alphaschema/`。
相同入口指定方法即可拟合全部 200／300 次模型，再评估历史 Test：

```bash
.venv/bin/python -u artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/evaluate_ready.py alphaschema
```

日志为 `alphaschema-evaluation.log`、`alphaschema-fit.log` 和 `alphaschema-test.log`。
该组于 2026-11-06 16:06（北京时间）完成，200／300 次分别新增 6／9 项，32／35 列
全部入模；配对 B26 拟合、共同样本和基线预测与前两组一致。
[完整结果](../../../docs/records/experiments.md#2026-11-06-haiku-b26alphaschema-配对评估与变换后-wmse)。

按用户后续要求，另算变换后标签 WMSE：从原 5,296,605 行 Train+Val 恢复标签统计，
用模型保存的 1%／99% 边界裁剪 Test 标签，再减各品种训练期裁剪后均值。86 个评分
品种中有 12 个训练期未见，明确采用零中心、仅裁剪。主结果保留原 86 品种与权重，
另报原 IC 的 80 品种对照；两个节点均重算原配置的 2,000 次时间块区间。
原预测与 `fit.json`、`test.json` 保留，IC 仍采用原始标签。

```bash
.venv/bin/python -u artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/recompute_transformed_wmse.py
```

标签统计保存为本轮产物目录的 `transformed_target.json`，全部误差结果与区间保存为
`transformed_wmse.json`，日志为 `transformed-wmse.log`。这是查看原始 Test 后按用户要求
补充的诊断口径，详见[变换后 WMSE 记录](../../../docs/records/experiments.md#2026-11-06-haiku-b26变换后标签-wmse-重算)。

追加 AlphaSchema 的变换后误差使用同一份已恢复的训练统计，不重新估计标签变换：

```bash
.venv/bin/python -u artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/recompute_transformed_wmse.py alphaschema
```

脚本核对配对 B26、共同训练行和标签边界一致，追加到 `transformed_wmse.json`，保留其他
方法的现有结果。日志为 `alphaschema-transformed-wmse.log`。

按用户后续更正，LightGBM 下游组合另按默认 `min_data_in_leaf=20`、`lambda_l2=0`
重训，复用本轮冻结搜索、200／300 次前缀、全部准入成员和训练期标签统计。
最新 Haiku 表使用这组结果；本目录及原 leaf2000／L2=1 模型保留为历史记录。
配置、保存模型及复跑入口见[默认参数重训](../zk26_lgbm_defaults_20261106/README.md)。

本轮运行标识：

| 方法 | Run ID 后缀 |
| --- | --- |
| AlphaPROBE | `a99c2b066d` |
| AlphaSchema | `8cc5576eb7` |
| MCTS-LLM | `64f65a23b9` |
| ReAct | `db9096e73d` |
| AlphaAtlas V3 | `61c42b9f7c` |

完整路径为 `artifacts/runs/futures_kernel_5m_full-all-fold2-<method>-42-<后缀>/`。
后台 tmux 会话为 `b26-haiku55-20261106`，总控日志为本轮产物目录的 `run.log`。
首次启动的 ReAct 子进程失败后，本轮由 `recover_react.py` 恢复该运行并负责后续配对评估，
日志为 `recovery.log`；其他四个进程继续原搜索。恢复流程等待五组全部冻结后才进入拟合和
Test；任一组失败或被停止则不评估。原总控保留首个 ReAct 子进程的失败退出码，不执行评估。
恢复会话为 `b26-haiku55-react-recovery-20261106`；历史失败日志追加保留。
