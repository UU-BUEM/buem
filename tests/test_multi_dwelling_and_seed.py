"""Tests for the occupancy/#14 (multi-dwelling diversification) and
occupancy/#15 (building_id-derived seed) fixes in
AttributeBuilder.generate_electricity_profile(). See CHANGELOG.md
[Unreleased] and occupancy's own CHANGELOG.md for the full rationale.
"""
import json
from pathlib import Path

import numpy as np

from buem.integration.scripts.attribute_builder import AttributeBuilder

project_root = Path(__file__).resolve().parent.parent
DUMMY_DIR = project_root / "src" / "buem" / "data" / "buildings" / "dummy"


def _load_building_attributes(fixture_name: str) -> dict:
    from buem.integration.scripts.geojson_validator import validate_geojson_request

    payload = json.loads((DUMMY_DIR / fixture_name).read_text(encoding="utf-8"))
    result = validate_geojson_request(payload)
    assert result.is_valid, [str(e) for e in result.get_errors()]
    feature = result.validated_data["features"][0]
    return feature["properties"]["buem"]["building_attributes"]


def _residential_attrs() -> dict:
    return _load_building_attributes("building_01_small_residential.json")


# ── occupancy/#15: building_id-derived seed ──────────────────────────────


def test_same_building_id_is_deterministic():
    attrs = _residential_attrs()
    merged_a = AttributeBuilder(payload_attrs=dict(attrs), building_id="buildingA").build()
    merged_b = AttributeBuilder(payload_attrs=dict(attrs), building_id="buildingA").build()
    assert merged_a["elecLoad"].equals(merged_b["elecLoad"])


def test_different_building_ids_diverge():
    attrs = _residential_attrs()
    merged_a = AttributeBuilder(payload_attrs=dict(attrs), building_id="buildingA").build()
    merged_b = AttributeBuilder(payload_attrs=dict(attrs), building_id="buildingB").build()
    assert not merged_a["elecLoad"].equals(merged_b["elecLoad"])


def test_explicit_seed_still_overrides_building_id():
    attrs = _residential_attrs()
    attrs["seed"] = 777
    merged_a = AttributeBuilder(payload_attrs=dict(attrs), building_id="buildingA").build()
    merged_b = AttributeBuilder(payload_attrs=dict(attrs), building_id="buildingB").build()
    assert merged_a["elecLoad"].equals(merged_b["elecLoad"])


def test_no_building_id_falls_back_to_occupancy_default():
    """Without a building_id, behavior is unchanged: two builds of the same
    attrs still agree (occupancy's own construction-input-derived default
    seed), since nothing here passes building_id at all."""
    attrs = _residential_attrs()
    merged_a = AttributeBuilder(payload_attrs=dict(attrs)).build()
    merged_b = AttributeBuilder(payload_attrs=dict(attrs)).build()
    assert merged_a["elecLoad"].equals(merged_b["elecLoad"])


# ── occupancy/#14: multi-dwelling diversification ────────────────────────


def test_multi_dwelling_total_energy_matches_single_household_scaled():
    """The diversified aggregate should still land close to one household's
    annual total multiplied by the dwelling count -- diversification
    changes the *shape* of the peak, not the building's overall energy
    use."""
    attrs = _residential_attrs()
    attrs["seed"] = 123

    single_attrs = dict(attrs)
    single_attrs["residential_units"] = 1.0
    single_merged = AttributeBuilder(
        payload_attrs=single_attrs, building_id="blockA"
    ).build()
    single_total = float(single_merged["elecLoad"].sum())

    multi_attrs = dict(attrs)
    multi_attrs["residential_units"] = 20.0
    multi_merged = AttributeBuilder(
        payload_attrs=multi_attrs, building_id="blockA"
    ).build()
    multi_total = float(multi_merged["elecLoad"].sum())

    assert np.isclose(multi_total, single_total * 20.0, rtol=0.15)


def test_multi_dwelling_diversifies_peak_relative_to_naive_scaling():
    """The whole point of occupancy/#14: summing distinct households
    yields a lower peak/mean ratio than multiplying one household's
    profile by the dwelling count."""
    attrs = _residential_attrs()
    attrs["seed"] = 123
    attrs["residential_units"] = 1.0
    single_merged = AttributeBuilder(
        payload_attrs=dict(attrs), building_id="blockA"
    ).build()
    naive_scaled = single_merged["elecLoad"] * 20.0
    naive_ratio = naive_scaled.max() / naive_scaled.mean()

    attrs["residential_units"] = 20.0
    diversified_merged = AttributeBuilder(
        payload_attrs=dict(attrs), building_id="blockA"
    ).build()
    diversified_ratio = (
        diversified_merged["elecLoad"].max() / diversified_merged["elecLoad"].mean()
    )

    assert diversified_ratio < naive_ratio


def test_multi_dwelling_is_deterministic_for_same_building_id():
    attrs = _residential_attrs()
    attrs["residential_units"] = 10.0
    merged_a = AttributeBuilder(payload_attrs=dict(attrs), building_id="blockA").build()
    merged_b = AttributeBuilder(payload_attrs=dict(attrs), building_id="blockA").build()
    assert merged_a["elecLoad"].equals(merged_b["elecLoad"])


def test_use_provided_elec_load_skips_multi_dwelling_path():
    """use_provided_elecLoad takes the single-household + flat-scale path
    even when residential_units > 1 -- elecLoad is a real measured series
    in that case, so it must come back untouched (just scaled by units,
    the prior behavior), not replaced by a diversified aggregate."""
    attrs = _residential_attrs()
    attrs["residential_units"] = 5.0

    weather_attrs = AttributeBuilder(payload_attrs=dict(attrs)).build()
    weather_index = weather_attrs["elecLoad"].index

    import pandas as pd

    measured = pd.Series(1.0, index=weather_index, name="elecLoad")
    attrs["elecLoad"] = measured
    attrs["use_provided_elecLoad"] = True
    merged = AttributeBuilder(payload_attrs=dict(attrs)).build()

    # A caller-supplied elecLoad is never rescaled (by units or
    # otherwise) -- it's a real measured series, left exactly as
    # measured. The point of this test is that it stays flat at 1.0/hour
    # rather than coming back as a diversified, hourly-varying shape.
    assert np.allclose(merged["elecLoad"].to_numpy(), 1.0)
