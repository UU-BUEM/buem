"""Compare buem's own hourly-dynamic heating result against a TABULA-style
seasonal-balance ("typology") estimate, for every real building in a
completed batch run, grouped by (building_type, construction_year_class).

This is a *method* comparison, not a data comparison (contrast with
``compare_era_type_vs_cbs.py``, which compares buem against real CBS
consumption): both numbers describe the exact same real building. buem's
own figure comes straight from the batch run's ``heating_kWh`` column,
computed by the full hourly 5R1C solve. The typology figure is computed
here by re-deriving each building's aggregated envelope conductance
(``H_tot`` -- the same quantity buem's own ``calcDesignHeatLoad()``
sums, obtained cheaply via ``ModelBUEM._initEnvelop()`` without
re-running the hourly solve) and combining it with that building's own
matched TABULA row's seasonal-method reference parameters (heating-season
length, reference external temperature, indoor setpoint, internal-gain
rate, and transmission-reduction factor). Both estimates therefore
describe the same building and the same envelope; they differ only in
method (TABULA's degree-day-style seasonal balance vs. buem's hourly
dynamic ISO 13790 solve).

The seasonal-balance formula (``tabula_degree_day_kwh_m2``) is the same
one already used by
``buem.analysis.netherlands.construction_year_stratification`` for a
single representative building per stratum; this script applies it to
every real building in a batch run instead, and reads ``H_tot`` and
``A_ref`` without re-running the hourly solve for each one.

Usage::

    python scripts/compare_typology_vs_buem.py results/loenen_gm0200_v2.parquet \\
        --data-dir src/buem/data/buildings/netherlands/Loenen \\
        --country NL --provider merra-2 --year 2018 \\
        --csv validation/NL/tabula/loenen_tabula_vs_buem_v2.csv
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from buem.analysis.netherlands.construction_year_stratification import (
    tabula_degree_day_kwh_m2,
)
from buem.analysis.provider_comparison import building_attrs_from
from buem.analysis.weather_providers import extract_provider_weather
from buem.buildings.datasources.csv_source import CsvBuildingSource
from buem.buildings.mapping.geometry_utils import region_center_lat_lon
from buem.buildings.mapping.lod2_mapper import LOD2Mapper
from buem.config.building_registry import DEFAULT_WEATHER_PROVIDER, DEFAULT_YEAR
from buem.config.cfg_building import CfgBuilding
from buem.config.reference_values import resolve_envelope_reference

logger = logging.getLogger(__name__)

_PER_BUILDING_ERRORS = (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, RuntimeError)


def _load_tabula_climate(data_dir: Path) -> dict[float, dict[str, float]]:
    """tabula_variant_code_id -> the seasonal-method reference parameters
    (HeatingDays, Theta_e, theta_i, phi_int, F_red_htr1) needed by
    :func:`tabula_degree_day_kwh_m2`."""
    tab = pd.read_csv(data_dir / "tabula.csv")
    cols = ["HeatingDays", "Theta_e", "theta_i", "phi_int", "F_red_htr1"]
    return tab.set_index("id")[cols].to_dict(orient="index")


def compute_h_tot(building, weather_df: pd.DataFrame, *, region_code: str | None) -> tuple[float, float] | None:
    """Returns (H_tot [kW/K], A_ref [m2]) for one building, without running
    the hourly solve -- ``ModelBUEM.calcDesignHeatLoad()`` calls
    ``_initEnvelop()`` internally (envelope parsing only) if ``self.bH`` is
    still empty."""
    from buem.integration.scripts.attribute_builder import AttributeBuilder
    from buem.thermal.model_buem import ModelBUEM

    attrs = dict(building_attrs_from(building))
    attrs["weather"] = weather_df
    attrs["use_provided_weather"] = True
    attrs["region_code"] = region_code
    merged = AttributeBuilder(payload_attrs=attrs).build()
    cfg = CfgBuilding(merged).to_cfg_dict()
    model = ModelBUEM(cfg)
    model.calcDesignHeatLoad()  # populates model.bH cheaply, no hourly solve
    h_tot = sum(model.bH[c].get("Original", 0.0) for c in model.bH if "Original" in model.bH[c])
    a_ref = float(cfg.get("A_ref") or building.computed_A_ref())
    return h_tot, a_ref


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("parquet", type=Path, help="Completed batch run (buem's own results).")
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="CsvBuildingSource directory the batch run used, e.g. "
                             "src/buem/data/buildings/netherlands/Loenen")
    parser.add_argument("--country", type=str, default="NL")
    parser.add_argument("--region-code", type=str, default=None)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--provider", type=str, default=DEFAULT_WEATHER_PROVIDER,
                        choices=["merra-2", "cosmo-rea6", "era5-land"])
    parser.add_argument("--min-n", type=int, default=1,
                        help="Only report strata with at least this many buildings.")
    parser.add_argument("--csv", type=Path, default=None, help="Write the table here.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    buem_df = pd.read_parquet(args.parquet)
    buem_df = buem_df[buem_df["status"] == "ok"]
    buem_df = buem_df[buem_df["building_type"].isin(["SFH", "TH", "MFH", "AB"])]
    logger.info("%s: %d ok residential row(s)", args.parquet.name, len(buem_df))

    source = CsvBuildingSource(args.data_dir)
    u_value_overrides = resolve_envelope_reference(None, args.data_dir, country=args.country)
    mapper = LOD2Mapper(source, country=args.country, u_value_overrides=u_value_overrides)
    climate = _load_tabula_climate(args.data_dir)

    bdf = source.buildings
    variant_map = bdf.set_index("building_feature_id")["tabula_variant_code_id"].to_dict()

    lat, lon = region_center_lat_lon(bdf)
    logger.info("Region center: (%.4f, %.4f)", lat, lon)
    weather_df = extract_provider_weather(lat, lon, args.year, providers=(args.provider,))[args.provider]

    rows = []
    n_total = len(buem_df)
    for i, rec in enumerate(buem_df.itertuples(index=False)):
        bid = int(rec.building_feature_id)
        if i % 200 == 0:
            logger.info("H_tot: %d/%d", i, n_total)
        variant_id = variant_map.get(bid)
        params = climate.get(variant_id) if variant_id is not None and pd.notna(variant_id) else None
        if params is None:
            continue
        building = mapper.map_building(bid)
        if building is None:
            continue
        try:
            result = compute_h_tot(building, weather_df, region_code=args.region_code)
        except _PER_BUILDING_ERRORS as exc:
            logger.warning("building_feature_id=%s: H_tot computation failed (%s: %s)", bid, type(exc).__name__, exc)
            continue
        if result is None:
            continue
        h_tot, a_ref = result
        if a_ref <= 0:
            continue
        typology_kwh_m2 = tabula_degree_day_kwh_m2(
            h_tot, a_ref,
            heating_days=params["HeatingDays"],
            theta_e=params["Theta_e"],
            theta_i=params["theta_i"],
            phi_int_w_m2=params["phi_int"],
            f_red_htr=params["F_red_htr1"],
        )
        rows.append(dict(
            building_feature_id=bid,
            building_type=rec.building_type,
            construction_year_class=rec.construction_year_class,
            A_ref=a_ref,
            H_tot=h_tot,
            buem_kwh_m2=float(rec.heating_kWh) / a_ref,
            typology_kwh_m2=typology_kwh_m2,
        ))

    per_building = pd.DataFrame(rows)
    logger.info("H_tot computed for %d/%d building(s)", len(per_building), n_total)

    grouped = (
        per_building.groupby(["building_type", "construction_year_class"])
        .agg(
            n=("building_feature_id", "count"),
            buem_kWh_m2=("buem_kwh_m2", "mean"),
            typology_kWh_m2=("typology_kwh_m2", "mean"),
        )
        .reset_index()
    )
    grouped = grouped[grouped["n"] >= args.min_n].copy()
    grouped["ratio"] = grouped["buem_kWh_m2"] / grouped["typology_kWh_m2"]
    grouped = grouped.rename(columns={"building_type": "type", "construction_year_class": "era"})
    order = {"SFH": 0, "TH": 1, "MFH": 2, "AB": 3}
    grouped = grouped.sort_values(
        by=["type", "era"], key=lambda s: s.map(order) if s.name == "type" else s,
    ).reset_index(drop=True)
    for col in ("buem_kWh_m2", "typology_kWh_m2", "ratio"):
        grouped[col] = grouped[col].round(2)

    print()
    print(f"buem (hourly dynamic) vs TABULA-method typology (seasonal balance, same envelope), kWh/m2/yr")
    print("=" * 82)
    print(grouped.to_string(index=False))
    print()
    print(f"  {len(grouped)} stratum/strata (min n={args.min_n}); {len(per_building)} building(s) total")

    if args.csv is not None:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        grouped.to_csv(args.csv, index=False)
        print(f"\nWrote {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
