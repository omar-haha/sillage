import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from sillage.core.money import dec
from sillage.strategy.blend import Blend


@pytest.fixture
def review() -> Any:
    spec = importlib.util.spec_from_file_location("balanced_review", "scripts/evaluate_balanced.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_variants_preserve_balanced_and_declare_all_controls(review: Any) -> None:
    spec = json.loads(Path("research/balanced-review-spec.json").read_text())
    assert spec["capital_usd"] == 25000
    assert len(spec["variants"]) == len(set(spec["variants"])) == 11
    for name in spec["variants"]:
        strategy, costs, capital = review.variant(name)
        assert capital == dec(100000 if name == "balanced-100k" else 25000)
        assert costs.min_commission == dec(
            "0.35" if name == "balanced-tiered" else 2 if name == "balanced-2x" else 1
        )
        if name.startswith("balanced"):
            assert isinstance(strategy, Blend)
            assert strategy.weights == (dec("0.5"), dec("0.5"))
    assert review.variant("blend-momentum-25")[0].weights == (dec("0.25"), dec("0.75"))
    assert review.variant("blend-momentum-75")[0].weights == (dec("0.75"), dec("0.25"))
    with pytest.raises(ValueError, match="undeclared"):
        review.variant("best-looking-neighbor")


def test_changed_vintage_aborts_before_scoring_or_writing(review: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr("sys.argv", ["evaluate_balanced.py"])
    monkeypatch.setattr(review, "sha", lambda path: "unexpected-vintage")
    monkeypatch.setattr(
        review, "run_backtest", lambda *args, **kwargs: pytest.fail("must not score")
    )
    with pytest.raises(ValueError, match="vintage changed"):
        review.main()


def test_window_retains_only_prior_denominator_and_scored_returns(review: Any) -> None:
    nav = pd.Series([100, 110, 121, 133.1], index=pd.bdate_range("2020-01-01", periods=4))
    selected = review.window(nav, "2020-01-03", "2020-01-06")
    assert selected.index[0] == pd.Timestamp("2020-01-02")
    assert len(selected.pct_change().dropna()) == 2
    assert selected.pct_change().dropna().sum() == pytest.approx(0.2)
    assert len(review.window(nav, "2020-01-01", "2020-01-06")) == 4
    with pytest.raises(ValueError, match="insufficient"):
        review.window(nav, "2030-01-01", "2030-01-02")


def test_factor_regression_recovers_known_exposures_without_future_fitting_rule(
    review: Any,
) -> None:
    rng = np.random.default_rng(42)
    index = pd.bdate_range("2020-01-01", periods=401)
    a, b = rng.normal(0, 0.01, 400), rng.normal(0, 0.005, 400)

    def curve(values: Any) -> pd.Series:
        return pd.Series(np.r_[100, 100 * np.cumprod(1 + values)], index=index)

    rates = pd.Series(0.0, index=index)
    result = review.factor_diagnostic(curve(0.4 * a + 0.6 * b), curve(a), curve(b), rates)
    assert result["spy_beta"] == pytest.approx(0.4)
    assert result["ief_beta"] == pytest.approx(0.6)
    assert result["r_squared"] == pytest.approx(1)
    with pytest.raises(ValueError, match="identical"):
        review.factor_diagnostic(curve(a).iloc[1:], curve(a), curve(b), rates)
