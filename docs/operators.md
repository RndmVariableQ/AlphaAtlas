# 基础算子目录

共 60 个规范名称；别名不重复计数。
由 `uv run python scripts/operator_catalog.py` 生成。数值与扩展协议见 [DSL](dsl.md)。

| 名称 | 参数类型 | 输出 | 作用域 | 历史规则 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `ABS` | series | series | elementwise | none | Absolute value of x. |
| `ADD` | series, series | series | elementwise | none | Elementwise x + y. |
| `AND` | condition, condition | condition | elementwise | none | Logical AND of two conditions. Either unknown input yields unknown. |
| `CLIP` | series, float, float | series | elementwise | none | Clamp x to the fixed lower and upper bounds, inclusive. |
| `CS_DEMEAN` | series | series | cs | none | Subtract the mean of eligible finite values at the same timestamp. |
| `CS_RANK` | series | series | cs | none | Ascending average rank / count among eligible finite values at the same timestamp. |
| `CS_SCALE` | series | series | cs | none | Divide x by sum(abs(x)) among eligible finite values. Null if total <= 1e-12. |
| `CS_WINSORIZE` | series, float=0.01, float=0.99 | series | cs | none | Clamp eligible x to same-timestamp lower/upper quantiles (linear interpolation). |
| `CS_ZSCORE` | series | series | cs | none | Cross-sectional (x - mean) / sample std among eligible finite values. Null if std <= 1e-12. |
| `DELAY` | series, window | series | ts | lag | Value of x n bars ago. Requires all n+1 observations to be finite. |
| `DELTA` | series, window | series | ts | lag | x minus its value n bars ago. Requires all n+1 observations to be finite. |
| `DIVIDE` | series, series | series | elementwise | none | Elementwise x / y. Null when abs(y) <= 1e-12. |
| `EQ` | series, series | condition | elementwise | none | Condition x == y. |
| `EXP` | series | series | elementwise | none | Exponential exp(x). Nonfinite results become null. |
| `FILLNA` | series, series | series | elementwise | none | Replace null x with y. Does not change market eligibility or target validity. |
| `GE` | series, series | condition | elementwise | none | Condition x >= y. |
| `GT` | series, series | condition | elementwise | none | Condition x > y. |
| `IF_THEN_ELSE` | condition, series, series | series | elementwise | none | Choose x when condition is true, y when false, null when unknown. |
| `IS_FINITE` | series | condition | elementwise | none | True for finite x, false for null or nonfinite x. |
| `LE` | series, series | condition | elementwise | none | Condition x <= y. |
| `LOG` | series | series | elementwise | none | Natural logarithm of x. Null for x <= 0. |
| `LOG1P` | series | series | elementwise | none | Natural logarithm of 1 + x. Null for x <= -1. |
| `LT` | series, series | condition | elementwise | none | Condition x < y. |
| `MAX` | series, series | series | elementwise | none | Elementwise maximum of x and y. Both inputs must be finite. |
| `MIN` | series, series | series | elementwise | none | Elementwise minimum of x and y. Both inputs must be finite. |
| `MULTIPLY` | series, series | series | elementwise | none | Elementwise x * y. |
| `NE` | series, series | condition | elementwise | none | Condition x != y. |
| `NEG` | series | series | elementwise | none | Negate x. |
| `NOT` | condition | condition | elementwise | none | Logical negation. An unknown condition stays unknown. |
| `OR` | condition, condition | condition | elementwise | none | Logical OR of two conditions. Either unknown input yields unknown. |
| `POWER` | series, float | series | elementwise | none | Raise x to a fixed exponent p. Invalid/nonfinite results become null. |
| `RETURN` | series, window | series | ts | lag | Simple return x / x[n bars ago] - 1, not log return. Requires n+1 valid bars. |
| `SIGN` | series | series | elementwise | none | Sign of x: -1 for negative, 0 for zero, +1 for positive. |
| `SIGNED_POWER` | series, float | series | elementwise | none | sign(x) * abs(x)**p for fixed p. Zero with p <= 0 yields null. |
| `SQRT` | series | series | elementwise | none | Square root of x. Null for x < 0. |
| `SUBTRACT` | series, series | series | elementwise | none | Elementwise x - y. |
| `TS_ALL` | condition, window | condition | ts | window | Whether all conditions are true over n bars. Requires a fully known window. |
| `TS_ANY` | condition, window | condition | ts | window | Whether any condition is true over n bars. Requires a fully known window. |
| `TS_ARGMAX` | series, window | series | ts | window | Bars since the maximum in the n-bar window. Current=0, nearest tie wins. |
| `TS_ARGMIN` | series, window | series | ts | window | Bars since the minimum in the n-bar window. Current=0, nearest tie wins. |
| `TS_CORR` | series, series, window | series | ts | window | Rolling Pearson correlation of x and y over n jointly valid bars. |
| `TS_COUNT` | condition, window | series | ts | window | Number of true conditions over n bars. Any unknown invalidates the window. |
| `TS_COV` | series, series, window | series | ts | window | Rolling sample covariance of x and y over n jointly valid bars, ddof=1. |
| `TS_KURT` | series, window | series | ts | window | Bias-corrected excess kurtosis over n bars (normal=0). Null for constant windows. |
| `TS_LINEAR_DECAY` | series, window | series | ts | window | Weighted mean over n bars, with weights 1 (oldest) through n (newest). |
| `TS_MAX` | series, window | series | ts | window | Maximum x over the last n bars. |
| `TS_MEAN` | series, window | series | ts | window | Arithmetic mean of x over the last n bars. |
| `TS_MEDIAN` | series, window | series | ts | window | Median of x over the last n bars. |
| `TS_MIN` | series, window | series | ts | window | Minimum x over the last n bars. |
| `TS_PROD` | series, window | series | ts | window | Product of x over the last n bars. |
| `TS_QUANTILE` | series, window, float | series | ts | window | Quantile q of x over n bars, using linear interpolation. |
| `TS_RANK` | series, window | series | ts | window | Current x's ascending average rank within n bars, divided by n. |
| `TS_RANKCORR` | series, series, window | series | ts | window | Rolling Spearman correlation, reranking x and y inside each n-bar window. |
| `TS_RATE` | condition, window | series | ts | window | Fraction of true conditions over n bars. Any unknown invalidates the window. |
| `TS_SKEW` | series, window | series | ts | window | Bias-corrected skewness over n bars. Constant windows yield null. |
| `TS_STD` | series, window | series | ts | window | Sample standard deviation over n bars, ddof=1. |
| `TS_SUM` | series, window | series | ts | window | Sum of x over the last n bars, including the current bar. |
| `TS_VAR` | series, window | series | ts | window | Sample variance over n bars, ddof=1. |
| `TS_WINSORIZE` | series, window, float=0.01, float=0.99 | series | ts | window | Clamp current x to its rolling lower/upper quantiles over n bars. |
| `TS_ZSCORE` | series, window | series | ts | window | (x - rolling mean) / rolling sample std over n bars. Null if std <= 1e-12. |
