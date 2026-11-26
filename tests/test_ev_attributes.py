"""Household electric-vehicle attributes: has_ev, ev_ownership_probability,
ev_charger_kw, ev_config, ev_charging_kW and elecLoad_includes_ev.

occupancy models the EV as its own sub-model; its home charging is kept
out of elecLoad (an internal heat gain) and returned as ev_charging_kW.
"""
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from buem.integration.scripts.attribute_builder import AttributeBuilder
from buem.integration.scripts.geojson_validator import validate_geojson_request

project_root = Path(__file__).resolve().parent.parent
DUMMY_DIR = project_root / "src" / "buem" / "data" / "buildings" / "dummy"


def _building_attributes() -> dict:
    payload = json.loads(
        (DUMMY_DIR / "building_01_small_residential.json").read_text(encoding="utf-8")
    )
    result = validate_geojson_request(payload)
    assert result.is_valid, [str(e) for e in result.get_errors()]
    return dict(
        result.validated_data["features"][0]["properties"]["buem"]["building_attributes"]
    )


def _resolver(**attrs) -> AttributeBuilder:
    """An AttributeBuilder with merged attributes set directly, for the
    option-resolution unit tests (no weather fetch, no generation)."""
    builder = AttributeBuilder(payload_attrs=attrs)
    builder.merge_sources()
    return builder


# -- option resolution (unit level) ------------------------------------------


def test_default_single_dwelling_has_an_ev():
    options, _ = _resolver()._resolve_ev_options(None, multi_dwelling=False)
    assert options.has_ev is True
    assert options.ev_config is None


def test_multi_dwelling_without_has_ev_draws_per_dwelling():
    options, _ = _resolver()._resolve_ev_options(None, multi_dwelling=True)
    assert options.has_ev is None
    explicit, _ = _resolver(has_ev=True)._resolve_ev_options(None, multi_dwelling=True)
    assert explicit.has_ev is True


def test_ownership_probability_makes_ownership_unknown():
    options, _ = _resolver(ev_ownership_probability=0.35)._resolve_ev_options(
        None, multi_dwelling=False,
    )
    assert options.has_ev is None
    assert options.ev_config.ownership_probability == pytest.approx(0.35)


def test_charger_power_override_targets_home_mode():
    options, _ = _resolver(ev_charger_kw=11.0)._resolve_ev_options(None, multi_dwelling=False)
    assert options.ev_config.home_mode.power_kw == pytest.approx(11.0)


def test_legacy_equipment_ev_charger_maps_to_has_ev(caplog):
    with caplog.at_level(logging.WARNING):
        options, spec = _resolver()._resolve_ev_options(
            {"ev_charger": False, "oven": True}, multi_dwelling=False,
        )
    assert options.has_ev is False
    assert spec == {"oven": True}
    assert any("deprecated" in record.getMessage() for record in caplog.records)
    # An explicit has_ev still wins over the legacy flag.
    options, _ = _resolver(has_ev=True)._resolve_ev_options(
        {"ev_charger": False}, multi_dwelling=False,
    )
    assert options.has_ev is True


def test_invalid_ev_inputs_raise():
    with pytest.raises(ValueError, match="has_ev"):
        _resolver(has_ev="yes")._resolve_ev_options(None, multi_dwelling=False)
    with pytest.raises(ValueError, match="Unknown EV config key"):
        _resolver(ev_config={"vehicle": {"battery_kwh": 70}})._resolve_ev_options(
            None, multi_dwelling=False,
        )


# -- full pipeline -------------------------------------------------------------


@pytest.fixture(scope="module")
def baseline() -> dict:
    return AttributeBuilder(payload_attrs=_building_attributes()).build()


def test_ev_charging_is_reported_separately_from_elec_load(baseline):
    ev = baseline["ev_charging_kW"]
    assert isinstance(ev, pd.Series)
    assert ev.index.equals(baseline["weather"].index)
    assert ev.sum() > 0

    attrs = _building_attributes()
    attrs["has_ev"] = False
    without = AttributeBuilder(payload_attrs=attrs).build()
    assert float(without["ev_charging_kW"].sum()) == 0.0
    # The EV never changes the internal-gain electricity load.
    pd.testing.assert_series_equal(without["elecLoad"], baseline["elecLoad"])


def test_ev_flexibility_series_are_exported(baseline):
    """Plugged-in share, driving energy and away charging -- the inputs an
    energy-system model needs to treat the EV as a flexible demand."""
    plugged = baseline["ev_plugged_in"]
    assert plugged.between(0.0, 1.0).all()
    assert baseline["ev_drive_kWh"].sum() > 0
    assert baseline["ev_away_charging_kWh"].sum() >= 0
    # Charging only happens while the car is plugged in.
    charging = baseline["ev_charging_kW"] > 0
    assert (plugged[charging] > 0).all()
    # Stored battery energy closes the hourly balance: charging at home
    # (x 0.92) and away (x 0.93) minus driving.
    stored = baseline["ev_stored_kWh"].to_numpy()
    net = (
        baseline["ev_charging_kW"].to_numpy() * 0.92
        + baseline["ev_away_charging_kWh"].to_numpy() * 0.93
        - baseline["ev_drive_kWh"].to_numpy()
    )
    assert abs(stored[1:] - stored[:-1] - net[1:]).max() < 1e-6


def test_meter_total_minus_measured_ev_gives_appliance_load(baseline):
    index = baseline["weather"].index
    measured_ev = pd.Series(0.0, index=index)
    measured_ev.iloc[18:22] = 7.4
    meter_total = pd.Series(0.5, index=index) + measured_ev

    attrs = _building_attributes()
    attrs.update(
        use_provided_elecLoad=True,
        elecLoad=meter_total,
        elecLoad_includes_ev=True,
        ev_charging_kW=measured_ev,
    )
    merged = AttributeBuilder(payload_attrs=attrs).build()
    assert np.allclose(merged["elecLoad"].to_numpy(), 0.5)
    assert np.allclose(merged["ev_charging_kW"].to_numpy(), measured_ev.to_numpy())
    # A measured charging series says nothing about trips: no simulated
    # flexibility series may be attached to it.
    assert "ev_drive_kWh" not in merged


def test_meter_total_with_simulated_ev_is_refused(baseline):
    attrs = _building_attributes()
    attrs.update(
        use_provided_elecLoad=True,
        elecLoad=pd.Series(0.5, index=baseline["weather"].index),
        elecLoad_includes_ev=True,
    )
    with pytest.raises(RuntimeError, match="requires a measured ev_charging_kW"):
        AttributeBuilder(payload_attrs=attrs).build()
