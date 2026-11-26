"""Existing on-site installations: has_pv, pv_capacity_kwp, has_battery,
battery_capacity_kwh, and a supplied PV output series (pv_kW).

These are building facts only. buem is a demand model: it computes neither
PV output nor battery operation (PV generation lives in
conversion-technologies, consistent with EnerPlanET's pysam-pv), and none
of these attributes changes the thermal results.
"""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from buem.config.validator import validate_cfg, validate_installations
from buem.integration.scripts.attribute_builder import AttributeBuilder
from buem.integration.scripts.geojson_validator import validate_geojson_request

project_root = Path(__file__).resolve().parent.parent
DUMMY_DIR = project_root / "src" / "buem" / "data" / "buildings" / "dummy"


def _merged(**attrs) -> dict:
    """Merged attributes after the installation-flag step (no weather
    fetch, no generation)."""
    builder = AttributeBuilder(payload_attrs=attrs)
    builder.merge_sources()
    builder._imply_installation_flags()
    return builder.merged_attrs


def _installation_issues(cfg: dict) -> list[str]:
    return [
        issue for issue in validate_cfg(cfg)
        if "pv" in issue or "battery" in issue
    ]


def _payload() -> dict:
    return json.loads(
        (DUMMY_DIR / "building_01_small_residential.json").read_text(encoding="utf-8")
    )


def _building_attributes() -> dict:
    result = validate_geojson_request(_payload())
    assert result.is_valid, [str(e) for e in result.get_errors()]
    return dict(result.validated_data["features"][0]["properties"]["buem"]["building_attributes"])


def test_default_is_no_pv_and_no_battery():
    merged = _merged()
    assert merged["has_pv"] is False
    assert merged["has_battery"] is False
    assert merged["pv_capacity_kwp"] is None
    assert merged["battery_capacity_kwh"] is None
    assert merged["pv_kW"] is None
    assert _installation_issues(merged) == []


def test_capacity_or_pv_series_without_flag_implies_the_flag():
    merged = _merged(pv_capacity_kwp=4.2, battery_capacity_kwh=10.0)
    assert merged["has_pv"] is True
    assert merged["has_battery"] is True
    assert _installation_issues(merged) == []
    series = pd.Series(0.0, index=pd.date_range("2018-01-01", periods=3, freq="h"))
    assert _merged(pv_kW=series)["has_pv"] is True


def test_flag_without_capacity_is_valid():
    merged = _merged(has_pv=True, has_battery=True)
    assert _installation_issues(merged) == []


def test_explicit_false_flag_with_capacity_is_rejected():
    merged = _merged(has_pv=False, pv_capacity_kwp=4.2)
    assert merged["has_pv"] is False
    assert _installation_issues(merged) == ["pv_capacity_kwp is set but has_pv is false"]


@pytest.mark.parametrize(
    "attrs, message",
    [
        ({"has_pv": "yes"}, "has_pv must be true or false"),
        ({"has_battery": 1}, "has_battery must be true or false"),
        ({"pv_capacity_kwp": 0}, "pv_capacity_kwp must be > 0"),
        ({"battery_capacity_kwh": -5}, "battery_capacity_kwh must be > 0"),
        ({"pv_capacity_kwp": "big"}, "pv_capacity_kwp must be a number"),
        ({"battery_capacity_kwh": True}, "battery_capacity_kwh must be a number"),
        ({"elecLoad_net_of_pv": True, "use_provided_elecLoad": True}, "elecLoad_net_of_pv needs pv_kW"),
        ({"elecLoad_net_of_pv": True, "pv_kW": [0.0]}, "elecLoad_net_of_pv needs use_provided_elecLoad"),
        (
            {"elecLoad_net_of_pv": True, "use_provided_elecLoad": True, "pv_kW": [0.0], "has_battery": True},
            "elecLoad_net_of_pv cannot be used with has_battery",
        ),
    ],
)
def test_invalid_values_are_reported(attrs, message):
    issues = validate_installations(_merged(**attrs))
    assert len(issues) == 1 and issues[0].startswith(message), issues


def test_build_rejects_bad_installation_before_any_generation():
    with pytest.raises(ValueError, match="pv_capacity_kwp"):
        AttributeBuilder(payload_attrs={"pv_capacity_kwp": -5}).build()


def test_full_build_carries_the_installation_and_leaves_thermal_inputs_alone():
    attrs = _building_attributes()
    plain = AttributeBuilder(payload_attrs=dict(attrs)).build()
    attrs.update(has_pv=True, pv_capacity_kwp=4.2, has_battery=True)
    equipped = AttributeBuilder(payload_attrs=attrs).build()

    assert equipped["has_pv"] is True and equipped["pv_capacity_kwp"] == 4.2
    assert equipped["has_battery"] is True and equipped["battery_capacity_kwh"] is None
    pd.testing.assert_series_equal(plain["Q_ig"], equipped["Q_ig"])
    pd.testing.assert_series_equal(plain["elecLoad"], equipped["elecLoad"])
    # buem computes no PV output of its own.
    assert equipped["pv_kW"] is None
    assert not any(key.startswith("pv_kW_per") for key in equipped)


# -- net meter reading + supplied PV output -----------------------------------


def _builder_with_pv(pv: object) -> AttributeBuilder:
    builder = AttributeBuilder(payload_attrs={})
    builder.merge_sources()
    builder.merged_attrs["pv_kW"] = pv
    return builder


def test_net_meter_reading_becomes_gross_use():
    weather_index = pd.date_range("2018-06-20", periods=24, freq="h") + pd.Timedelta(minutes=30)
    pv = pd.Series(0.0, index=weather_index)
    pv.iloc[12] = 3.0
    hours = weather_index - pd.Timedelta(minutes=30)
    net = pd.Series(0.5, index=hours, name="elecLoad")
    net.iloc[12] = -2.0  # exporting at noon
    net.iloc[13] = -0.1  # exporting more than the PV series says: clipped to 0
    gross = _builder_with_pv(pv)._add_pv_to_net_meter(net)
    assert gross.iloc[12] == pytest.approx(1.0)
    assert gross.iloc[13] == 0.0
    assert gross.iloc[0] == pytest.approx(0.5)
    assert gross.name == "elecLoad"


def test_net_meter_needs_a_pv_series():
    index = pd.date_range("2018-01-01", periods=3, freq="h")
    with pytest.raises(ValueError, match="pv_kW"):
        _builder_with_pv(None)._add_pv_to_net_meter(pd.Series(0.5, index=index))
    with pytest.raises(TypeError, match="pandas Series"):
        _builder_with_pv([1.0, 2.0, 3.0])._add_pv_to_net_meter(pd.Series(0.5, index=index))


# -- request forwarding and region export --------------------------------------


def test_request_fields_are_forwarded():
    payload = _payload()
    building = payload["features"][0]["properties"]["buem"]["building"]
    sent = {
        "has_ev": None, "ev_ownership_probability": 0.3, "ev_charger_kw": 11.0,
        "has_pv": True, "pv_capacity_kwp": 4.2,
        "has_battery": True, "battery_capacity_kwh": 10.0,
    }
    building.update(sent)
    result = validate_geojson_request(payload)
    assert result.is_valid, [str(e) for e in result.get_errors()]
    attrs = result.validated_data["features"][0]["properties"]["buem"]["building_attributes"]
    assert {key: attrs[key] for key in sent} == sent


def _run_region_hourly():
    path = project_root / "scripts" / "run_region_hourly.py"
    spec = importlib.util.spec_from_file_location("run_region_hourly", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_region_script_reads_installation_columns_from_the_building_table():
    script = _run_region_hourly()
    row = pd.Series({
        "building_feature_id": 1, "has_pv": "true", "pv_capacity_kwp": 5.0,
        "has_battery": float("nan"), "battery_capacity_kwh": None,
    })
    assert script._installation_attrs(row) == {"has_pv": True, "pv_capacity_kwp": 5.0}
    assert script._installation_attrs(None) == {}
    assert script._installation_attrs(pd.Series({"has_battery": 0})) == {"has_battery": False}
    with pytest.raises(ValueError, match="has_pv"):
        script._installation_attrs(pd.Series({"has_pv": "maybe"}))
    assert {"has_pv", "pv_capacity_kwp", "has_battery", "battery_capacity_kwh", "roof_faces"} <= set(
        script._SUMMARY_COLUMNS
    )
    assert not any(column.startswith("pv_") for column in script._HOURLY_COLUMNS)


def test_region_script_exports_roof_faces_for_a_downstream_pv_model():
    script = _run_region_hourly()
    cfg = {"components": {"Roof": {"elements": [
        {"id": "r1", "tilt": 40, "azimuth": 180, "area": 30.0},
        {"id": "r2", "tilt": 0, "azimuth": None, "area": 50.0},
        {"id": "bad", "tilt": None, "area": 5.0},
    ]}}}
    faces = json.loads(script._roof_faces_json(cfg))
    assert faces == [
        {"id": "r1", "tilt": 40.0, "azimuth": 180.0, "area_m2": 30.0},
        {"id": "r2", "tilt": 0.0, "azimuth": None, "area_m2": 50.0},
    ]
    assert script._roof_faces_json({"components": {}}) is None


def test_region_script_exports_bag_id_and_wgs84_centroid():
    import pyproj

    from buem.buildings.datasources.cityjson_extractor import _encode_point_ewkb

    script = _run_region_hourly()
    x, y = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:28992", always_xy=True).transform(6.02, 52.12)
    row = pd.Series({
        "building_feature_id": 7,
        "bag_pand_id": "NL.IMBAG.Pand.0200100000021000",
        "building_centroid_geom": _encode_point_ewkb(x, y),
    })
    assert script._location_attrs(row) == {
        "bag_pand_id": "NL.IMBAG.Pand.0200100000021000", "latitude": 52.12, "longitude": 6.02,
    }
    empty = {"bag_pand_id": None, "latitude": None, "longitude": None}
    assert script._location_attrs(None) == empty
    assert script._location_attrs(pd.Series({"bag_pand_id": float("nan"), "building_centroid_geom": None})) == empty
    assert {"bag_pand_id", "latitude", "longitude"} <= set(script._SUMMARY_COLUMNS)
