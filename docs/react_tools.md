# 公共研究工具与输入输出示例

随机搜索、GP、MCTS、Atlas、ReAct 和 AlphaPROBE 复用同一个 SearchSession：**get_context、library_search、library_get、library_list、library_stats、evaluate**。前五项由 session.query 分发；候选评估由 runner 调用 session.evaluate 原子提交，session.evaluation_result 生成一致的模型反馈，避免查询期间私自评估或重复扣费。

工具内容、数据权限、预算与准入实现统一，各方法的搜索编排仍保留：ReAct AgentScope 单轮多工具循环，AlphaPROBE 三阶段候选生成，符号基线使用有限语法。开放参考库不等于自动注入种子。

## 配置与预算

- 所有方法的参考库开关统一来自 configs/benchmark.toml 的 reference_library。空字符串关闭。
- get_context 包含全部稳定研究元数据，**不含 remaining_attempts**；固定提示词也不注入预算数值。
- remaining_attempts 仅在评估结果中出现，包括成功、重复和失败的评估。只读查询和错误回复不追加预算。
- 平台内部仍维护实时预算，并在耗尽时停止。上下文不随预算变化，有利于复用稳定前缀；此处没有测量推理服务的 KV cache 命中率。
- ReAct 由 AgentScope 发送标准 tools 请求，可在一轮返回多个工具调用；候选由 runner 逐个原子评估，下一轮收到聚合的 evaluation_result。AlphaPROBE 通过 queries 请求同一批只读工具。

## 检查结果

使用独立内存中的 256 根合成期货 5m Bar 和 24 个字段，真实执行 JSON 分发、数值评估、准入和临时 trial 落盘。不调用模型服务或行情 API，指标不代表真实市场表现。[完整输入输出记录](../artifacts/prompt-previews/shared-tool-examples.json)。

| 检查项 | 结果 |
| --- | --- |
| 六种方法的公共工具 | 相同输入得到相同 context、库查询和评估结果 |
| 参考库开关 | 开启／关闭均对所有方法一致 |
| context 稳定性 | 评估前后序列化内容相同；ReAct 固定提示词相同 |
| 实时预算 | 首次评估返回 9，重复评估返回 8；查询不含预算 |
| 数据边界 | 不返回 test 标签或运行身份；修改返回对象不会污染内部配置 |
| 只读与惰性加载 | get_context 不加载参考公式，查询不扣预算 |

## 1. get_context

一次获取 asset、frequency、target、metric、fields、operators、expression_rules、evaluation_rules、reference_library。算子包含完整定义与说明，规则来自当前实验配置。

输入：

```json
{
  "tool": "get_context",
  "arguments": {}
}
```

输出节选：保留全部顶层字段和 24 个字段名；60 个完整算子定义中仅展示 TS_MEAN。表达式规则另含 timeframes / numeric_rules，评估规则另含计算说明；完整内容在原始记录中。

```json
{
  "tool_result": {
    "asset": "futures_curve",
    "frequency": "5m",
    "target": {
      "price_field": "close",
      "horizon_bars": 12,
      "return_type": "log_return",
      "boundary": "same_instrument_and_continuity_segment"
    },
    "metric": "time_series_weighted_pearson_ic",
    "fields": [
      "open",
      "high",
      "low",
      "close",
      "volume",
      "amount",
      "open_interest",
      "days_to_maturity",
      "open_p1",
      "high_p1",
      "low_p1",
      "close_p1",
      "volume_p1",
      "amount_p1",
      "open_interest_p1",
      "days_to_maturity_p1",
      "open_p2",
      "high_p2",
      "low_p2",
      "close_p2",
      "volume_p2",
      "amount_p2",
      "open_interest_p2",
      "days_to_maturity_p2"
    ],
    "operators": [
      {
        "name": "TS_MEAN",
        "args": [
          "series",
          "window"
        ],
        "scope": "ts",
        "output": "series",
        "defaults": [],
        "history": "window",
        "version": "1",
        "kind": "builtin",
        "parameter_names": [],
        "description": "Arithmetic mean of x over the last n bars.",
        "history_bars": 0,
        "window_arg": null,
        "history_offset": -1,
        "minimum_window": 1,
        "aliases": [
          "MEAN"
        ]
      }
    ],
    "expression_rules": {
      "max_nodes": 100,
      "max_depth": 20
    },
    "evaluation_rules": {
      "primary_metric": "time_series_weighted_pearson_ic",
      "quality": {
        "train_and_val_ic_gt": 0.005
      },
      "min_coverage": 0.8,
      "deduplication": {
        "metric": "validation_pooled_spearman",
        "absolute_correlation_lt": 0.7,
        "min_common_finite_rows": 100
      }
    },
    "reference_library": {
      "enabled": true,
      "name": "futures_cta",
      "bars_per_day": 1,
      "semantics": "Original daily window numbers count native bars. Definitions are loaded only when searched; no pre-admission. Use explicit @15m/@30m/@60m/@1d for other frequency scopes."
    }
  }
}
```

本次记录中评估前后的两次 get_context 输出完全一致，剩余预算已从 10 降至 9，但不会写入 context。

## 2. library_search

检索本运行因子或参考定义。source 为 reference / run / all；query 为空时浏览。这里只返回参考公式、来源与缺失字段情况，没有参考绩效或自动准入。

输入：

```json
{
  "tool": "library_search",
  "arguments": {
    "source": "reference",
    "query": "tsmom_63",
    "limit": 1
  }
}
```

输出（实际返回）：

```json
{
  "tool_result": {
    "total": 1,
    "offset": 0,
    "limit": 1,
    "reference_enabled": true,
    "results": [
      {
        "source": "reference",
        "admitted": false,
        "library": "futures_cta",
        "version": "futures_cta_dsl_v2",
        "source_url": "https://github.com/quantskills/skill-futures-cta-alpha",
        "source_commit": "a9c1feddaec41984d58e8a15ae5678743c769c2a",
        "name": "tsmom_63",
        "source_name": "tsmom_63",
        "family": "momentum",
        "expression": "RETURN($close, 63)",
        "fields": [
          "close"
        ],
        "missing_fields": [],
        "status": "available",
        "note": ""
      }
    ]
  }
}
```

## 3. evaluate

提交表达式并由平台统一评估、扣预算和判断准入。下面的 remaining_attempts 位于 tool_result 内。

输入：

```json
{
  "tool": "evaluate",
  "arguments": {
    "expression": "$close",
    "name": "Synthetic close",
    "hypothesis": "Protocol fixture only."
  }
}
```

输出节选：保留全部顶层字段，12 项指标仅展示主指标的 train / val / val_raw 三项。

```json
{
  "tool_result": {
    "candidate": {
      "expression": "$close",
      "region_ids": [],
      "hypothesis": "Protocol fixture only.",
      "name": "Synthetic close"
    },
    "factor_id": "7ef72cdb18d877200da9b742a002315f8aecc1e1e87e05d212664983e42dd09e",
    "status": "success",
    "metrics": [
      {
        "name": "time_series_weighted_pearson_ic",
        "split": "train",
        "value": -0.9818475279301531,
        "n_obs": 116,
        "aggregation": "time_series_weighted_pearson_ic",
        "n_groups": 1
      },
      {
        "name": "time_series_weighted_pearson_ic",
        "split": "val",
        "value": 0.9818475279301531,
        "n_obs": 116,
        "aggregation": "time_series_weighted_pearson_ic",
        "n_groups": 1
      },
      {
        "name": "time_series_weighted_pearson_ic",
        "split": "val_raw",
        "value": -0.9818475279301531,
        "n_obs": 116,
        "aggregation": "time_series_weighted_pearson_ic",
        "n_groups": 1
      }
    ],
    "direction": -1,
    "coverage": 1.0,
    "failure_reason": null,
    "accepted": true,
    "reason": "accepted",
    "max_abs_corr": null,
    "nearest_factor": null,
    "comparison_complete": true,
    "library_version": 1,
    "trial_index": 1,
    "remaining_attempts": 9
  }
}
```

## 4. library_list

分页列出本次运行已入库成员和主验证指标。offset 从 0 开始，limit 为 1–100。

输入：

```json
{
  "tool": "library_list",
  "arguments": {
    "offset": 0,
    "limit": 1
  }
}
```

输出（实际返回）：

```json
{
  "tool_result": {
    "total": 1,
    "members": [
      {
        "factor_id": "7ef72cdb18d877200da9b742a002315f8aecc1e1e87e05d212664983e42dd09e",
        "candidate": {
          "expression": "$close",
          "region_ids": [],
          "hypothesis": "Protocol fixture only.",
          "name": "Synthetic close"
        },
        "primary_val_ic": 0.9818475279301531
      }
    ]
  }
}
```

## 5. library_get

用已返回的 factor_id 查询成员表达式、方向、覆盖率和训练／验证报告。

输入：

```json
{
  "tool": "library_get",
  "arguments": {
    "factor_id": "7ef72cdb18d877200da9b742a002315f8aecc1e1e87e05d212664983e42dd09e"
  }
}
```

输出节选：保留全部顶层字段，12 项指标仅展示主指标的 train / val / val_raw 三项。

```json
{
  "tool_result": {
    "candidate": {
      "expression": "$close",
      "region_ids": [],
      "hypothesis": "Protocol fixture only.",
      "name": "Synthetic close"
    },
    "factor_id": "7ef72cdb18d877200da9b742a002315f8aecc1e1e87e05d212664983e42dd09e",
    "status": "success",
    "metrics": [
      {
        "name": "time_series_weighted_pearson_ic",
        "split": "train",
        "value": -0.9818475279301531,
        "n_obs": 116,
        "aggregation": "time_series_weighted_pearson_ic",
        "n_groups": 1
      },
      {
        "name": "time_series_weighted_pearson_ic",
        "split": "val",
        "value": 0.9818475279301531,
        "n_obs": 116,
        "aggregation": "time_series_weighted_pearson_ic",
        "n_groups": 1
      },
      {
        "name": "time_series_weighted_pearson_ic",
        "split": "val_raw",
        "value": -0.9818475279301531,
        "n_obs": 116,
        "aggregation": "time_series_weighted_pearson_ic",
        "n_groups": 1
      }
    ],
    "direction": -1,
    "coverage": 1.0,
    "failure_reason": null,
    "version": 1
  }
}
```

## 6. library_stats

读取本次运行成员数和库版本，参考定义不计入成员数。

输入：

```json
{
  "tool": "library_stats",
  "arguments": {}
}
```

输出（实际返回）：

```json
{
  "tool_result": {
    "members": 1,
    "version": 1
  }
}
```

## 边界与失败示例

### 成员不存在

输入：

```json
{
  "tool": "library_get",
  "arguments": {
    "factor_id": "missing"
  }
}
```

输出（实际返回）：

```json
{
  "tool_result": null
}
```

### 分页超出成员数量

输入：

```json
{
  "tool": "library_list",
  "arguments": {
    "offset": 100,
    "limit": 1
  }
}
```

输出（实际返回）：

```json
{
  "tool_result": {
    "total": 1,
    "members": []
  }
}
```

### 已移除的零散查询工具

输入：

```json
{
  "tool": "get_fields",
  "arguments": {}
}
```

输出（实际返回）：

```json
{
  "tool_result": {
    "error": "Unknown tool; only the listed evaluation and read-only tools exist",
    "instruction": "Correct the JSON tool request."
  }
}
```

### 检索 limit 超限

输入：

```json
{
  "tool": "library_search",
  "arguments": {
    "source": "reference",
    "limit": 21
  }
}
```

输出（实际返回）：

```json
{
  "tool_result": {
    "error": "offset must be >=0 and limit in [1,20]",
    "instruction": "Correct the JSON tool request."
  }
}
```

### 重复表达式：拒绝入库并更新剩余预算

输入：

```json
{
  "tool": "evaluate",
  "arguments": {
    "expression": "$close"
  }
}
```

输出节选：保留全部顶层字段，12 项指标仅展示主指标的 train / val / val_raw 三项。

```json
{
  "tool_result": {
    "candidate": {
      "expression": "$close",
      "region_ids": [],
      "hypothesis": "",
      "name": null
    },
    "factor_id": "7ef72cdb18d877200da9b742a002315f8aecc1e1e87e05d212664983e42dd09e",
    "status": "success",
    "metrics": [
      {
        "name": "time_series_weighted_pearson_ic",
        "split": "train",
        "value": -0.9818475279301531,
        "n_obs": 116,
        "aggregation": "time_series_weighted_pearson_ic",
        "n_groups": 1
      },
      {
        "name": "time_series_weighted_pearson_ic",
        "split": "val",
        "value": 0.9818475279301531,
        "n_obs": 116,
        "aggregation": "time_series_weighted_pearson_ic",
        "n_groups": 1
      },
      {
        "name": "time_series_weighted_pearson_ic",
        "split": "val_raw",
        "value": -0.9818475279301531,
        "n_obs": 116,
        "aggregation": "time_series_weighted_pearson_ic",
        "n_groups": 1
      }
    ],
    "direction": -1,
    "coverage": 1.0,
    "failure_reason": null,
    "accepted": false,
    "reason": "expression_duplicate",
    "max_abs_corr": null,
    "nearest_factor": null,
    "comparison_complete": true,
    "library_version": 1,
    "trial_index": 2,
    "remaining_attempts": 8
  }
}
```

## 测试与实现入口

公共实现：src/alpha_atlas/session.py。跨方法一致性与稳定 context 测试：tests/test_research_tools.py。
ReAct／AlphaPROBE／MCTS-LLM 的调用、冻结与恢复分别由对应方法测试覆盖。
各自的查询轮数限制保留：ReAct 连续 10 次非评估回复停止；AlphaPROBE 每阶段最多
8 轮、每轮 16 项；MCTS-LLM 每阶段默认共 8 轮查询／格式纠正，每轮最多 16 项查询。

2026-09-12 公共反馈增加 `diagnostics` 字段：开启 `search_diagnostics` 时返回
train 的 ICIR、有效日数、持仓换手及单位／有效转移数；关闭时为空对象。
`get_context.evaluation_rules.search_diagnostics` 一次提供相同定义和启用版本。
上面的历史示例未展开新字段，既有字段语义不变；完整诊断语义见
[MCTS-LLM 共享诊断定义](mcts_llm.md)。公共编译预检 `validate_expression` 供方法内部
使用，不新增模型 JSON 工具或数据权限，不评估或扣尝试。

```powershell
pwsh -File scripts/check.ps1
```
