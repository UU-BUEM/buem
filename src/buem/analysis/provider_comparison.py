"""
Run one building through buem's full simulation pipeline once per weather
provider, for direct comparison.

Uses the same canonical direct-construction path
``tests/test_building_types.py`` already exercises: ``Building
.to_v3_geojson_feature()`` -> ``validate_geojson_request()`` ->
``AttributeBuilder(payload_attrs=...).build()`` -> ``CfgBuilding(merged)
.to_cfg_dict()`` -> ``ModelBUEM(cfg).sim_model()``. Weather is supplied
directly (``use_provided_weather=True``) rather than re-fetched per
provider inside ``AttributeBuilder`` -- callers extract once via
``weather_providers.extract_provider_weather()`` and reuse the same
per-provider DataFrames across as many buildings as they like.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from buem.buildings.building import Building
from buem.config.cfg_building import CfgBuilding
from buem.integration.scripts.attribute_builder import AttributeBuilder
from buem.integration.scripts.geojson_validator import validate_geojson_request
from buem.thermal.model_buem import ModelBUEM

logger = logging.getLogger(__name__)


@dataclass
class ProviderRunResult:
    """One building's simulation output for one weather provider."""

    provider: str
    weather: pd.DataFrame  # T/GHI/DHI/DNI actually used, for input comparison
    heating_kW: pd.Series
    cooling_kW: pd.Series  # buem's own sign convention: <= 0
    elec_kW: pd.Series
    building_type: str
    construction_period: str
    A_ref: float


def building_attrs_from(building: Building) -> dict:
    """v3-GeoJSON round-trip through the real validator/converter, exactly
    like a live API request -- not a hand-shortcut, so this exercises the
    same conversion path (parent_id/azimuth normalization, TABULA-enum
    validation, etc.) real traffic does.

    Public (not module-private) because ``batch.py`` reuses it too, for
    the same per-building attrs-construction step run once per weather
    provider here vs. once per building there.
    """
    feature = building.to_v3_geojson_feature()
    payload = {"type": "FeatureCollection", "features": [feature]}
    result = validate_geojson_request(payload)
    if not result.is_valid:
        raise ValueError(
            f"Building {building.identity.building_feature_id} failed v3 "
            f"validation: {[str(e) for e in result.get_errors()]}"
        )
    assert result.validated_data is not None  # guaranteed once is_valid is True
    return result.validated_data["features"][0]["properties"]["buem"]["building_attributes"]


def run_building_across_providers(
    building: Building,
    provider_weather: dict[str, pd.DataFrame],
    *,
    use_milp: bool = False,
) -> dict[str, ProviderRunResult]:
    """Run *building* once per entry in *provider_weather*.

    Parameters
    ----------
    building : Building
        A mapped building (e.g. from ``building_selection
        .select_household_buildings()``).
    provider_weather : dict[str, pandas.DataFrame]
        provider -> T/GHI/DHI/DNI DataFrame, e.g. from
        ``weather_providers.extract_provider_weather()``. All providers
        must share the same location/year for the comparison to be
        meaningful -- not itself enforced here.
    use_milp : bool
        Passed through to ``ModelBUEM.sim_model()``.

    Returns
    -------
    dict[str, ProviderRunResult]
    """
    base_attrs = building_attrs_from(building)
    results: dict[str, ProviderRunResult] = {}

    for provider, weather_df in provider_weather.items():
        attrs = dict(base_attrs)
        attrs["weather"] = weather_df
        attrs["use_provided_weather"] = True

        # occupancy/#15 -- see buem.analysis.batch._process_one_building's
        # comment.
        merged = AttributeBuilder(
            payload_attrs=attrs,
            building_id=str(building.identity.building_feature_id),
        ).build()
        cfg = CfgBuilding(merged).to_cfg_dict()
        model = ModelBUEM(cfg)
        model.sim_model(use_milp=use_milp)

        idx = cfg["weather"].index
        results[provider] = ProviderRunResult(
            provider=provider,
            weather=weather_df,
            heating_kW=pd.Series(model.heating_load, index=idx, name="heating_kW"),
            cooling_kW=pd.Series(model.cooling_load, index=idx, name="cooling_kW"),
            elec_kW=pd.Series(merged["elecLoad"].to_numpy(), index=idx, name="elec_kW"),
            building_type=merged.get("building_type", ""),
            construction_period=merged.get("construction_period", ""),
            A_ref=float(merged.get("A_ref", 0.0)),
        )

    return results


__all__ = ["ProviderRunResult", "building_attrs_from", "run_building_across_providers"]
