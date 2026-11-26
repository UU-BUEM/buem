"""
Tests for AttributeBuilder's "don't silently model the wrong building"
guarantees: missing required attributes, db_fetcher failures, and profile
misalignment must all raise rather than silently substituting generic
defaults or zero-filled data.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from buem.integration.scripts.attribute_builder import AttributeBuilder, _reindex_or_raise
from buem.integration.scripts.geojson_validator import validate_geojson_request

project_root = Path(__file__).resolve().parent.parent
DUMMY_DIR = project_root / "src" / "buem" / "data" / "buildings" / "dummy"


def _load_building_attributes(fixture_name: str) -> dict:
    payload = json.loads((DUMMY_DIR / fixture_name).read_text(encoding="utf-8"))
    result = validate_geojson_request(payload)
    assert result.is_valid, [str(e) for e in result.get_errors()]
    feature = result.validated_data["features"][0]
    return feature["properties"]["buem"]["building_attributes"]


def test_missing_required_attrs_raises():
    """An empty payload (no location/geometry) must raise, not silently
    model the generic example house."""
    with pytest.raises(ValueError, match="latitude"):
        AttributeBuilder(payload_attrs={}).build()


def test_partial_payload_missing_geometry_raises():
    """Location alone isn't enough -- geometry (components/A_ref) is also required."""
    with pytest.raises(ValueError, match="components"):
        AttributeBuilder(payload_attrs={"latitude": 52.0, "longitude": 5.0}).build()


def test_db_fetcher_failure_raises():
    """A wired db_fetcher that fails for a specific building_id must raise,
    not silently continue with generic defaults."""
    def _broken_fetcher(building_id):
        raise OSError("database unreachable")

    with pytest.raises(RuntimeError, match="db_fetcher failed"):
        AttributeBuilder(
            payload_attrs={"latitude": 52.0, "longitude": 5.0, "A_ref": 100.0, "components": {}},
            building_id="b1",
            db_fetcher=_broken_fetcher,
        ).build()


def test_capacity_string_is_cast_to_int():
    """A capacity value arriving as a string (e.g. from a JSON payload) must
    still work, exercising the explicit int() cast added alongside num_persons."""
    building_attrs = _load_building_attributes("building_02_medium_office.json")
    building_attrs["capacity"] = "25"  # str, as it might arrive from a loose JSON client
    merged = AttributeBuilder(payload_attrs=building_attrs).build()
    assert "elecLoad" in merged


def test_capacity_garbage_string_raises_clear_error():
    """A non-numeric capacity must raise a clear conversion error, not an
    unrelated TypeError from ServiceBuildingProfile's internal <= 0 check."""
    building_attrs = _load_building_attributes("building_02_medium_office.json")
    building_attrs["capacity"] = "not-a-number"
    with pytest.raises(RuntimeError, match="invalid literal"):
        AttributeBuilder(payload_attrs=building_attrs).build()


def test_reindex_or_raise_rejects_misaligned_series():
    """_reindex_or_raise must raise instead of zero-filling when a profile
    doesn't actually cover the target index."""
    target_index = pd.date_range("2018-01-01", periods=24, freq="h")
    misaligned = pd.Series(1.0, index=pd.date_range("2019-01-01", periods=24, freq="h"))
    with pytest.raises(ValueError, match="could not be aligned"):
        _reindex_or_raise(misaligned, target_index, "Q_ig")


def test_reindex_or_raise_passes_through_aligned_series():
    target_index = pd.date_range("2018-01-01", periods=24, freq="h")
    aligned = pd.Series(range(24), index=target_index, dtype=float)
    result = _reindex_or_raise(aligned, target_index, "Q_ig")
    assert result.equals(aligned)


def test_reindex_or_raise_matches_the_same_hour_on_half_hour_weather():
    """Occupancy labels an hour by its start (00:00), the weather by its
    midpoint (00:30). Each weather hour must get the same hour's value --
    not the next hour's, and the year's last hour must not be repeated."""
    hours = pd.date_range("2018-01-01", periods=48, freq="h")
    weather = hours + pd.Timedelta(minutes=30)
    profile = pd.Series(range(48), index=hours, dtype=float)
    result = _reindex_or_raise(profile, weather, "Q_ig")
    assert result.index.equals(weather)
    assert result.tolist() == list(range(48))


def test_reindex_or_raise_matches_half_hour_series_onto_hour_index():
    """The reverse direction (a caller's half-hour meter series onto
    occupancy's on-the-hour index) pairs the same hours too."""
    hours = pd.date_range("2018-01-01", periods=24, freq="h")
    meter = pd.Series(range(24), index=hours + pd.Timedelta(minutes=30), dtype=float)
    result = _reindex_or_raise(meter, hours, "elecLoad")
    assert result.tolist() == list(range(24))


def test_reindex_or_raise_rejects_sub_hourly_series():
    index = pd.date_range("2018-01-01", periods=48, freq="30min")
    with pytest.raises(ValueError, match="more than one value"):
        _reindex_or_raise(pd.Series(1.0, index=index), index, "Q_ig")
