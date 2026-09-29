# 搜索方法

当前能力按实现陈述，实验结果集中在[实验记录](../records/experiments.md)。
所有方法遵循[接入规范](../method_reproduction.md)，公共接口见[研究工具](../research_tools.md)。

| CLI 名称 | 搜索机制 | 实现边界 |
| --- | --- | --- |
| `random` | 固定文法随机采样 | 有限候选空间的基线 |
| `gp` | 种群、选择、交叉与变异 | 固定模板／类型文法，不是任意深度程序树 |
| `mcts` | UCT 符号搜索 | 表达式结构基线，不是 LLM-MCTS |
| `atlas` | 区域 UCB 预算分配 | 区域导航原型，不是完整开放式地图 |
| [alphaprobe](alphaprobe.md) | 演化图、贝叶斯检索、三阶段 LLM 生成 | 本地算法适配，需 Chat 与 embedding 服务 |
| [mcts_llm](mcts_llm.md) | 公式树、五维奖励、定向改进与 FSA | 本地算法适配，需 Chat 服务和共享训练诊断 |
| [react](react.md) | 单 Agent 工具循环与上下文压缩 | 自建 LLM 基线，需支持 tools 的 Chat 服务 |

前四项共享有限文法；LLM 方法可使用更广 DSL，比较时必须披露搜索空间、初始化、
参考库和模型成本差异。方法奖励不替代公共准入，接入也不代表复现论文收益。

源码位于 [methods/](../../src/alpha_atlas/methods/)，方法参数位于 [configs/](../../configs/)。
新增方法只有在机制、来源、差异、测试与入口明确后才加入本表；未排期候选不扩展文档目录。
