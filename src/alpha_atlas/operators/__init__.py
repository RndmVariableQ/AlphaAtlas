"""Versioned, run-local operators. Validated group_batch kernels use local Numba."""

from alpha_atlas.operators.registry import OperatorDefinition, OperatorRegistry, OperatorSpec

__all__ = ["OperatorDefinition", "OperatorRegistry", "OperatorSpec"]
