# 因子库预测模型

搜索运行：zookeeper26-fold2-435d201527。主指标：time_series_weighted_pearson_ic。

这是独立的下游模型实验，不回写搜索结果，不是交易收益回测。
linear 为品种固定效应最小二乘回归；LightGBM 使用固定参数，无自动调参或早停。
模型与预处理在 train+val 合并集拟合；test 仅用于最终冻结后汇报。
test 仅使用保存的模型，不重新训练、选因子或翻转预测方向。

模型拟合与搜索启动的源码记录一致：否。源码记录仅用于来源说明，不校验当前源码。
输入因子 26 个，有效列 26 个，训练行 5296605。

缺失值按训练均值填补；训练常量／全缺失列移除；所有有效因子均缺失的行不预测。
特征与训练标签按拟合区间全局 P1–P99 裁剪；特征以裁剪后的均值和标准差处理，test 标签保持原值。
Linear 对特征和标签按品种在整个拟合区间去均值，并冻结预测偏移；LightGBM 使用全局预处理特征和按品种去均值的标签。
训练目标按样本等权拟合，指标聚合沿用资产主指标；两者权重口径不同。

| 模型 | split | 指标 | IC | 覆盖率 | 预测行数 / 可评估行数 |
| --- | --- | --- | --- | --- | --- |

## 因子列

| 列 | 因子 ID | 使用 |
| --- | --- | --- |
| factor_0 | 9c9c185f4236892beafbe7299674c467ed058dae04b8f84074b0508555df7dce | 是 |
| factor_1 | e1c5937577943987b2eb1d1cbc0e05afcba2775c5e584d119c4ab6f825dd42e3 | 是 |
| factor_2 | f0091ae273204e30bbc4ef2480a1ecfc4fa45bd3729cc97c5153023b535fa555 | 是 |
| factor_3 | c2a8d2f4eb0beab952a9c4a3fcc518b7ef451fc1b0f984a0e388abc4c1dabfdc | 是 |
| factor_4 | 4620eb6f7333bd0c74ddd4923893d09eee432906cc7a151fd600ba6090559e1a | 是 |
| factor_5 | 2e2691ebfb33ff3b565e61ca08417351980c1fee0dabdd8527c062fa11c7558b | 是 |
| factor_6 | 2986ead82b9d4f6c597b0d3d4727e2a3e51ce0067181b89b7793859191bb7670 | 是 |
| factor_7 | 391875065e047d932cef4593374620e23e2e1efe26d1dd7194e8741632625c29 | 是 |
| factor_8 | 57e8d50d9be6c43b83e58a79d965b4b6f6ab225f87aa36df964ab4544f8d1d4e | 是 |
| factor_9 | b455c94f85c8982454c9e7478a38467f9ece8aa8ff1f72fa66edff129dec17c9 | 是 |
| factor_10 | 6e8b82bb8968a475d59bc991c57a67bc92ad511a917a4d9b56e301b4946ad85d | 是 |
| factor_11 | 4c287504c495b21f9e960db48c2fac414e175d7be46b81508d38ecc0a226b1da | 是 |
| factor_12 | 7d772b81c8af6a3198cf87574040fab8d3f4f5bda2ce0cea50f6452e2e9553a1 | 是 |
| factor_13 | 756fa2f17667a53428a5b48831cfad3fb3ec1dfe9ea5f77a978288fdf461f183 | 是 |
| factor_14 | 7e783525d4bbb2adbad27814e2b9d02fa2d23f5532619f8567242696bb4c9d67 | 是 |
| factor_15 | fb6064462d3a301d860b252463a2c29a94dba952faa22179d8205285bf0b6692 | 是 |
| factor_16 | 7438b787a4f115706794f4359d4c05987adc8c8d1c1ddd5ca0e92601165cc357 | 是 |
| factor_17 | 6005030d7be2c77936dbad6f3d2180d04ccbfa9b39ef81da79428b22ec3fd1e2 | 是 |
| factor_18 | 2be8a5b4853aa607f5235ece09142bc6348d306be48422d9f19d6caf55945e02 | 是 |
| factor_19 | 64e342e39d1c0b8cb59c04919051af7898a489ffb2d289dc7178c772dd3ea980 | 是 |
| factor_20 | 083cb7e8d65ff728ae28953a9e08b78935ce7762d3115597c29a3d04a8a7bf88 | 是 |
| factor_21 | c0f1ad185ab22f08fa018fff68770883b0c97e272874dd0d34132f968d7e1a4f | 是 |
| factor_22 | b693ae9733eadc9e9ca24e2c34bd15ef641003a0e1e88fded956e341e8fa38d7 | 是 |
| factor_23 | ab183aff7816f29e8eb69d2155289d37b60491e7d4017cbfacfb5e7b33c404a5 | 是 |
| factor_24 | 87b0564b835de52b2fb7694a3753031c27a2b072ef40ba0ba6d720748ea49ea1 | 是 |
| factor_25 | 3bd6d4a98411a6ffd2d3ba4cacb108efee83d02f58ebac9a8f3f119e81c327db | 是 |

模型、预处理和配置：[fit.json](fit.json)。
