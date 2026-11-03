import json

import numpy as np
import pytest

from alpha_atlas.evaluation import prediction_error_summary


def test_prediction_error_positive_reduction_from_scalar_baseline():
    result = prediction_error_summary([1, 2, 3], [0.5, 2, 2.5], 0)
    assert result == pytest.approx(
        {
            "n": 3,
            "sse": 0.5,
            "baseline_sse": 14,
            "mse": 1 / 6,
            "baseline_mse": 14 / 3,
            "relative_mse_reduction": 27 / 28,
        }
    )
    assert json.loads(json.dumps(result, allow_nan=False)) == result


def test_prediction_error_negative_reduction_from_prediction_baseline():
    result = prediction_error_summary(np.array([1, 3]), np.array([0, 6]), [2, 2])
    assert result == {
        "n": 2,
        "sse": 10.0,
        "baseline_sse": 2.0,
        "mse": 5.0,
        "baseline_mse": 1.0,
        "relative_mse_reduction": -4.0,
    }


@pytest.mark.parametrize("prediction", [[1, 1], [2, 2]])
def test_prediction_error_zero_baseline_error_is_undefined(prediction):
    result = prediction_error_summary([1, 1], prediction, 1)
    assert result["baseline_sse"] == 0
    assert result["baseline_mse"] == 0
    assert result["relative_mse_reduction"] is None
    assert json.loads(json.dumps(result, allow_nan=False)) == result


def test_prediction_error_block_sums_reconstruct_pooled_error():
    first = prediction_error_summary([1, 2], [0, 2], 0)
    second = prediction_error_summary([3], [2], 0)
    full = prediction_error_summary([1, 2, 3], [0, 2, 2], 0)
    for key in ("n", "sse", "baseline_sse"):
        assert first[key] + second[key] == full[key]
    assert full["mse"] == pytest.approx(2 / 3)
    assert full["relative_mse_reduction"] == pytest.approx(6 / 7)


@pytest.mark.parametrize(
    ("target", "prediction", "baseline", "message"),
    [
        (1, [1], 0, "target must be one-dimensional"),
        ([1], 1, 0, "prediction must be one-dimensional"),
        ([[1]], [1], 0, "target must be one-dimensional"),
        ([1], [[1]], 0, "prediction must be one-dimensional"),
        ([1], [1], [[0]], "baseline must be one-dimensional"),
        ([], [], 0, "target must be nonempty"),
        ([1, 2], [1], 0, "prediction must have the same length"),
        ([1, 2], [1, 2], [0], "baseline must have the same length"),
        ([1, 2], [1, 2], [], "baseline must have the same length"),
    ],
)
def test_prediction_error_rejects_empty_misaligned_and_multidimensional_inputs(
    target, prediction, baseline, message
):
    with pytest.raises(ValueError, match=message):
        prediction_error_summary(target, prediction, baseline)


@pytest.mark.parametrize("name", ["target", "prediction", "baseline"])
@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_prediction_error_rejects_nonfinite_observations_without_dropping(name, invalid):
    inputs = {"target": [1, 2], "prediction": [1, 2], "baseline": [0, 0]}
    inputs[name] = [1, invalid]
    with pytest.raises(ValueError, match=f"{name} must contain only finite values"):
        prediction_error_summary(**inputs)


@pytest.mark.parametrize("invalid", [np.nan, np.inf])
def test_prediction_error_rejects_nonfinite_scalar_baseline(invalid):
    with pytest.raises(ValueError, match="baseline must contain only finite values"):
        prediction_error_summary([1], [1], invalid)


@pytest.mark.parametrize("invalid", [np.array([1 + 2j]), ["not a number"]])
def test_prediction_error_rejects_nonreal_input(invalid):
    with pytest.raises(ValueError, match="prediction must contain real numeric values"):
        prediction_error_summary([1], invalid, 0)


def test_prediction_error_rejects_overflow_from_finite_inputs():
    with pytest.raises(ValueError, match="prediction error computation overflowed"):
        prediction_error_summary([1e308], [-1e308], 0)


def test_product_weighted_error_averages_product_mse_before_comparing_baseline():
    # A has two rows, B one row. Product weights are 1 and 3, not per-row turnover.
    result = prediction_error_summary([1, 1, 2], [0, 1, 1], 0, weights=[0.5, 0.5, 3])
    assert result["mse"] == pytest.approx((1 * 0.5 + 3 * 1) / 4)
    assert result["baseline_mse"] == pytest.approx((1 * 1 + 3 * 4) / 4)
    assert result["relative_mse_reduction"] == pytest.approx(19 / 26)
    assert result["relative_mse_reduction"] != pytest.approx((1 * 0.5 + 3 * 0.75) / 4)
    assert result["weight_sum"] == 4
    assert json.loads(json.dumps(result, allow_nan=False)) == result


def test_weighted_error_is_invariant_to_weight_scale_and_reconstructs_blocks():
    target, prediction, weights = [1, 2, 3], [0, 2, 1], [0.5, 0, 3]
    whole = prediction_error_summary(target, prediction, 0, weights=weights)
    scaled = prediction_error_summary(target, prediction, 0, weights=np.array(weights) * 10)
    for key in ("mse", "baseline_mse", "relative_mse_reduction"):
        assert whole[key] == pytest.approx(scaled[key])
    a = prediction_error_summary(target[:1], prediction[:1], 0, weights=weights[:1])
    b = prediction_error_summary(target[1:], prediction[1:], 0, weights=weights[1:])
    for key in ("sse", "baseline_sse", "weight_sum"):
        assert whole[key] == pytest.approx(a[key] + b[key])
    assert whole["mse"] == pytest.approx(whole["sse"] / whole["weight_sum"])


@pytest.mark.parametrize(
    "weights,message",
    [
        (1, "weights must be one-dimensional"),
        ([[1, 2]], "weights must be one-dimensional"),
        ([1], "weights must have the same length"),
        ([1, -1], "weights must be nonnegative"),
        ([0, 0], "weights must have positive sum"),
        ([1, np.nan], "weights must contain only finite values"),
        ([1, np.inf], "weights must contain only finite values"),
        ([1, 2j], "weights must contain real numeric values"),
    ],
)
def test_weighted_error_rejects_invalid_weights(weights, message):
    with pytest.raises(ValueError, match=message):
        prediction_error_summary([1, 2], [1, 2], 0, weights=weights)


def test_weighted_error_rejects_overflow_and_keeps_zero_error_baseline_undefined():
    with pytest.raises(ValueError, match="prediction error computation overflowed"):
        prediction_error_summary([1, 2], [0, 0], 0, weights=[1e308, 1e308])
    assert (
        prediction_error_summary([1, 1], [0, 0], 1, weights=[1, 3])["relative_mse_reduction"]
        is None
    )
