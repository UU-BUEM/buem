"""Summer window ventilation (``n_air_window_max``, night flushing).

Opt-in: the default (0) must leave every existing result unchanged. When
on, windows may only cool (never heat) and never raise heating demand.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import numpy as np
import pytest

project_root = Path(__file__).resolve().parent.parent
os.environ.setdefault("BUEM_WEATHER_DIR", str(project_root / "src" / "buem" / "data" / "weather"))

from buem.config.cfg_attribute import cfg as DEFAULT_CFG  # noqa: E402
from buem.thermal.model_buem import ModelBUEM  # noqa: E402


def _run(**overrides) -> ModelBUEM:
    cfg = copy.deepcopy(DEFAULT_CFG)
    cfg["comfortT_ub"] = 26.0
    cfg.update(overrides)
    model = ModelBUEM(cfg)
    model.sim_model(use_milp=False)
    return model


@pytest.fixture(scope="module")
def baseline() -> ModelBUEM:
    return _run()


@pytest.fixture(scope="module")
def ventilated() -> ModelBUEM:
    return _run(n_air_window_max=3.0)


def test_default_is_off_and_matches_explicit_zero(baseline):
    explicit = _run(n_air_window_max=0.0)
    assert not baseline.window_vent_load.any()
    np.testing.assert_allclose(explicit.Q_HC, baseline.Q_HC)


def test_window_ventilation_cuts_cooling_without_raising_heating(baseline, ventilated):
    cool_before = -baseline.cooling_load.sum()
    cool_after = -ventilated.cooling_load.sum()
    assert cool_before > 0
    assert cool_after < cool_before
    # Windows are optional, so heating can only stay the same (solver tolerance).
    assert ventilated.heating_load.sum() <= baseline.heating_load.sum() * 1.001 + 0.1


def test_windows_only_ever_cool(ventilated):
    """Open only where indoor is warmer than outdoor, and within the
    physical limit H_window * (T_air - T_e)."""
    t_e = DEFAULT_CFG["weather"]["T"].to_numpy()
    open_hours = ventilated.window_vent_load > 0
    assert open_hours.any()
    delta = ventilated.T_air[open_hours] - t_e[open_hours]
    assert np.all(delta >= -1e-6)
    limit = ventilated.bH_window_max * delta
    assert np.all(ventilated.window_vent_load[open_hours] <= limit + 1e-4)


def test_window_free_pass_is_kept_for_checking(baseline, ventilated):
    np.testing.assert_allclose(ventilated.Q_HC_no_window, baseline.Q_HC, atol=1e-2)


def test_negative_rate_rejected():
    with pytest.raises(ValueError, match="n_air_window_max"):
        _run(n_air_window_max=-1.0)


def test_milp_path_refuses_window_ventilation():
    cfg = copy.deepcopy(DEFAULT_CFG)
    cfg["n_air_window_max"] = 2.0
    with pytest.raises(ValueError, match="only implemented in the LP path"):
        ModelBUEM(cfg).sim_model(use_milp=True)
