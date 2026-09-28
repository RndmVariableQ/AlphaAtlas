# Alpha Atlas development rules

- Python 3.13, Polars, 100-character line width. Run `pwsh -File scripts/check.ps1`.
- This is a separate static train/val factor-search project. Futures use native RiceQuant 5m data
  requested by the user; do not change ChaoticTrader's canonical 1m dataset or its databases.
- Asset adapters/exporters alone may read vendor APIs or Parquet. Research/methods use MarketData.
- Parquet exchange files use `.parq`, live under `data/`, and are excluded from Git.
- Never copy credentials into this repository or log credentials. Tests never access real credentials
  or vendor APIs. Provider setup is an explicit data acquisition operation.
- Features and targets are separated. Methods receive expressions, metadata, and train/val reports,
  never test labels. OOS is evaluated only after freezing a run.
- Instrument identity is `(exchange, instrument_id)`. Futures windows and targets never cross contracts.
  `product` is only an aggregation key. Continuous/mapped vendor IDs stay inside adapters.
- Daily stock index membership must be point-in-time; 1800 = HS300 + ZZ500 + ZZ1000.
  Preserve announcement/effective-date provenance for fundamentals and disclose revision limitations.
- Each `(asset, dataset, universe, fold, method, seed, run_id)` has an independent library/map.
- Distinguish baseline implementations from paper replications; do not claim LLM-MCTS or AGI is
  implemented when only a protocol or a symbolic baseline exists.
- No trading/order APIs. Do not fabricate missing data, substitute resampled data for native 5m,
  or call an incomplete acquisition complete. Manifests record coverage, missing partitions, and source.

## 实现范围与代码组织

- 所有方法的接入、复现和比较遵循 `docs/method_reproduction.md`。
  公共平台统一数据、DSL、数值评估、准入、预算和冻结；方法保留搜索机制及奖励组合。
  论文、作者代码、第三方实现和本地适配须区分，偏离与未实现核心机制写入方法说明。

- 所有方法复用 SearchSession 的研究工具：get_context、library_search、library_get、
  library_list、library_stats，以及由 runner 提交的 evaluate。模型可见 get_context 一次
  返回资产、频率、目标、主指标、允许字段、完整算子、表达式／评估规则和参考库设置。
  remaining_attempts 只放在评估结果中，不进入模型 context 或固定提示词；平台内部
  SearchContext 仍含预算用于停止控制。参考库开关统一放在 benchmark.toml，进入现有规则
  冻结校验；方法可选择不同搜索和查询策略，但不另建工具实现或方法专属的数据权限。
  日期区间、fold、运行/快照 ID 和 universe 留在平台内部，不进入 context 或 LLM 提示词。
  平台不设窗口或累计历史上限，保留正整数、
  算子最小窗口、完整有效窗口及真实合约/连续段隔离规则。

- 简洁、克制是实现约束，不只是文档措辞。在保留当前基本功能、正确性和性能的前提下，
  选择改动最少、依赖最少、容易读懂的实现；不以“更稳健”“将来可能需要”为由扩大范围。
- 优先复用现有接口、记录和提交流程。参考 AlphaSeeker、ChaoticTrader 时只复制必要逻辑，
  不搬入整套框架、依赖链和重复实现，不修改相邻项目或数据。
- 目录按实际职责组织，相关小功能集中放置。不为单个算子或小对象单独建文件，
  不为仅有一个调用点的流程引入通用框架、管理器、注册中心或多层抽象。
- 新机制必须对应用户明确要求或当前可复现的问题；没有实际需要时不增加任务队列、
  事件系统、通用幂等层、额外状态文件、审计账本或兼容迁移层。
- 保留已有配置、因子／算子身份和冻结校验机制，但不得自行叠加新的哈希体系。
  源码指纹仅保留来源记录，不用于阻止续跑、OOS 或已保存模型的复用；不恢复源码一致性校验。
  尤其不要增加检查点校验和、算子记录集合指纹、成员缓存哈希或重复的方法源码哈希。
  能用现有 ID、版本、trial 编号及直接内容比较解决的，就使用现有机制。
- 中断恢复复用原子 trial 和方法状态；缓存按需读取，缺失时重建。
  不把额外全文件扫描、重复校验或强制重算作为删除冗余机制后的替代品。
- 每步做好与需求相关的数值、边界、失败和回归测试，运行 scripts/check.ps1。
  周全测试不等于增加生产机制：测试应验证既有承诺，而非为未经要求的新机制扩大范围。
- 修改后同步更新文档，删除被替代的代码和依赖，保留旧运行产物。
  如发现可选增强项，默认留待后续，不先实现再以“安全／完整性”为由保留。
