"""Independent search methods sharing the Atlas evaluator and DSL."""

from alpha_atlas.methods.baselines import make_method as make_baseline


def make_method(name: str, seed: int, options=None):
    if name == "v1_async":
        from alpha_atlas.methods.v1_async import V1AsyncConfig, V1AsyncSearch

        return V1AsyncSearch(seed, V1AsyncConfig.from_mapping(options or {}))
    if name == "v1":
        from alpha_atlas.methods.v1 import V1Config, V1Search

        return V1Search(seed, V1Config.from_mapping(options or {}))
    if name == "mcts_llm":
        from alpha_atlas.methods.mcts_llm import MCTSLLMConfig, MCTSLLMSearch

        return MCTSLLMSearch(seed, MCTSLLMConfig.from_mapping(options or {}))
    if name == "annealing":
        from alpha_atlas.methods.annealing import AnnealingLLMConfig, AnnealingLLMSearch

        return AnnealingLLMSearch(seed, AnnealingLLMConfig.from_mapping(options or {}))
    if name == "react":
        from alpha_atlas.methods.react import ReactConfig, ReactSearch

        return ReactSearch(seed, ReactConfig.from_mapping(options or {}))
    if name == "alphaprobe":
        from alpha_atlas.methods.alphaprobe import AlphaProbeConfig, AlphaProbeSearch

        return AlphaProbeSearch(seed, AlphaProbeConfig.from_mapping(options or {}))
    return make_baseline(name, seed)


__all__ = ["make_method"]
