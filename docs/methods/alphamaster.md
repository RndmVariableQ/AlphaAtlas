# AlphaMaster 在线双链搜索

入口为 `--method alphamaster`，运行标识为 `alphamaster_atlas_v1`。本方法适配作者仓库默认
`run_dual_chain.py → run_pipeline → run_iteration` 路径，使用 Atlas 的市场、表达式和评估接口。

## 来源和适配口径

Wang 等的 KDD 2026 论文描述 RL、GFlowNet、LLM、GP 的候选来源和反馈优化链。
作者公开仓库的默认在线循环调用 LLM 生成和改写；另一个标准化评估入口读取已有公式文件并生成互补因子。
本项目的 Luna／Haiku 对比采用在线循环。独立的离线生成器、作者已挖出的公式及历史收益不参与初始化。
作者 README 指明研究用途，商业使用需取得许可。本适配独立实现，沿用本项目模型客户端。
[论文](https://doi.org/10.1145/3770855.3818120) · [作者固定提交](https://github.com/yinshuo-thu/AlphaMaster/tree/36a848682a3ea58ed154c2abd67414cb8dfc88c9/dual_chain)

每五个初始提案共用一份参考快照。有效参考按 Val 主 IC 降序取前三，废弃参考从一次改写后仍失败的
候选中按 Val 主 IC 升序取前三，缺失测量排在有测量的记录之后，并列沿用入库或提交顺序。
每个未通过的初始候选最多改写一次。改写提示携带原式、假说、拒绝原因、覆盖率、相关性和 Train／Val IC。
正常生成和定向改写都由指定模型实际完成，候选分别提交平台扣费。批内按生成、测量、必要时改写
的顺序串行执行，预算尾部由 runner 截止。格式修复仅纠正输出协议，超过次数提交失败候选。
已保存的模型响应在恢复时复用，原子 trial 与 runner 检查点负责对齐反馈。

上游有效池使用 IC、Sharpe 和年化收益阈值。本地用公共正式准入作为有效／改写分支判定，
有效参考的 Val IC 使用平台的 Train 定向值。缺失的收益指标不作插补。
这个替换使筛选还受覆盖率和库内去重影响。B26 的已有成员由平台完成初始测量，允许作为有效参考；
空库从无正例开始。运行中没有策略下单或 Python 表达式动态执行。

## 实验与验证

四个冻结版本采用 fold2、seed 42。空库使用八字段、1000 次尝试，B26 使用 34 字段、
26 个初始成员、300 次新增尝试。每个条件各跑 Luna 和 Haiku 一次。空库采用主表固定
Linear／LightGBM；B26 使用配对模型、Val 末品种名单，在 200／300 次节点评估相对初始库的增量。

合成测试通过正常 runner 构造实例，通过模拟模型验证双链切换、废弃参考、预算计数及
模型回复保存后中断恢复。[测试](../../tests/test_alphamaster.py)

用冻结版本单独启动空库搜索，或按 B26 比较范围启动搜索。以下以 Luna 为例；
Haiku 对应的版本名和文件名将 `luna` 换为 `haiku55`。四组的 run ID 见实验记录。

```bash
uv run atlas run --version rq8_fold2_alphamaster_luna_empty_1000 --search-only
uv run atlas experiment configs/experiments/alphamaster_fold2/luna.json --search-only
```

搜索冻结后，空库用 `atlas finish <run_dir>` 生成单因子、组合和相似度报告。
B26 用下列命令复用同一运行，先固定模型，再打开历史 Test；同一批比较的各组都应完成拟合后再执行第二条。

```bash
uv run atlas experiment configs/experiments/alphamaster_fold2/luna.json
uv run atlas experiment configs/experiments/alphamaster_fold2/luna.json --test
```
