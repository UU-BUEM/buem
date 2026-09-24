"""Hourly-resolution companion to ``buem.analysis.batch``.

``buem.analysis.batch`` retains only each building's *annual* totals (one
row per building) -- adequate for population-scale validation, but not for
the single-building/community-profile/diversity analyses in the journal
paper, which need every building's full 8760-hour trajectory (node
temperatures, heating/cooling load, solar/internal gain split, and
electricity), not just its yearly sum.

This script re-runs the identical per-building mapping-and-solve step
``batch._process_one_building`` uses (same ``LOD2Mapper``, same
``AttributeBuilder``/``CfgBuilding``/``ModelBUEM`` path, same shared
weather DataFrame fetched once per run), but also writes each building's
hourly series to a second, long-format Parquet file:

    building_feature_id, time, T_e, T_air, T_m, T_sur,
    Q_heat_kW, Q_cool_kW, Q_sol_win_kW, Q_sol_opq_kW, Q_int_kW, elec_kW

alongside the usual per-building annual summary (mirroring
``batch._RESULT_COLUMNS`` plus the dead-band/peak-hour/mean-temperature
figures the single-building writeup reports).

Intended for a caller-supplied, already-known building id list (e.g. the
"ok" residential ids of a prior ``batch.run_batch()`` population run) via
``--building-ids-file``, so the hourly run reproduces exactly the same
population rather than re-deriving it.

CLI
---
    python -m scripts.run_region_hourly \\
        --data-dir src/buem/data/buildings/netherlands/Heeten \\
        --country NL --region-code GM0177 \\
        --building-ids-file results/heeten_hourly_ids.txt \\
        --output results/heeten_hourly.parquet \\
        --summary-output results/heeten_hourly_summary.parquet
"""
from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from buem.analysis.batch import (
    BatchConfig,
    _batch_weather,
    _load_region_table,
    _load_u_value_overrides,
    _residential_units,
    _source_row,
    _worker_init,
    build_source,
)
from buem.config.building_registry import (
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    DEFAULT_WEATHER_PROVIDER,
    DEFAULT_YEAR,
)

logger = logging.getLogger(__name__)

_PER_BUILDING_ERRORS = (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, RuntimeError)

_HOURLY_COLUMNS = [
    "building_feature_id", "time", "T_e", "T_air", "T_m", "T_sur",
    "Q_heat_kW", "Q_cool_kW", "Q_sol_win_kW", "Q_sol_opq_kW", "Q_int_kW", "elec_kW",
]

_SUMMARY_COLUMNS = [
    "building_feature_id", "status", "error",
    "building_type", "construction_period", "A_ref", "n_walls", "n_exposed",
    "residential_units", "neighbour_status", "construction_year_class",
    "matched_via_label", "refurbishment_variant", "residential_units_source",
    "heating_kWh", "cooling_kWh", "elec_kWh", "dhw_kWh",
    "peak_heat_kW", "peak_hour", "heating_hours", "cooling_hours",
    "deadband_hours", "T_air_mean",
]

_PASSTHROUGH_COLUMNS = (
    "neighbour_status", "construction_year_class", "matched_via_label",
    "refurbishment_variant", "residential_units_source",
)


def _process_one_building_hourly(building_feature_id: int, use_milp: bool) -> tuple[dict[str, Any], pd.DataFrame | None]:
    """Map, build, and simulate one building, returning both its annual
    summary row and its full hourly trajectory (``None`` if it errored or
    was skipped)."""
    from buem.analysis.building_selection import wall_exposure
    from buem.analysis.provider_comparison import building_attrs_from
    from buem.config.cfg_building import CfgBuilding
    from buem.integration.scripts.attribute_builder import AttributeBuilder
    from buem.thermal.model_buem import ModelBUEM

    from buem.analysis import batch as _batch_mod

    row: dict[str, Any] = {col: None for col in _SUMMARY_COLUMNS}
    row["building_feature_id"] = building_feature_id

    try:
        mapper = _batch_mod._WORKER_MAPPER
        assert mapper is not None, "_worker_init() must run before this (pool initializer)"
        building = mapper.map_building(building_feature_id)
        if building is None:
            row["status"] = "skipped"
            row["error"] = "LOD2Mapper.map_building() returned None (no TABULA match or unmappable geometry)"
            return row, None

        exposure = wall_exposure(building)
        row["building_type"] = building.identity.building_type
        row["construction_period"] = building.identity.construction_period
        row["A_ref"] = round(building.computed_A_ref(), 2)
        row["n_walls"] = exposure.n_walls
        row["n_exposed"] = exposure.n_exposed

        source_row = _source_row(building_feature_id)
        units = _residential_units(source_row)
        row["residential_units"] = units
        for col in _PASSTHROUGH_COLUMNS:
            if source_row is not None and col in source_row:
                value = source_row[col]
                row[col] = None if pd.isna(value) else str(value)

        attrs = dict(building_attrs_from(building))
        attrs["weather"] = _batch_mod._WORKER_WEATHER
        attrs["use_provided_weather"] = True
        attrs["residential_units"] = units
        attrs["region_code"] = _batch_mod._WORKER_REGION_CODE

        merged = AttributeBuilder(payload_attrs=attrs).build()
        cfg = CfgBuilding(merged).to_cfg_dict()
        model = ModelBUEM(cfg)
        model.sim_model(use_milp=use_milp)

        times = cfg["weather"].index
        heat_kW = np.asarray(model.heating_load, dtype=float)
        cool_kW = np.asarray(model.cooling_load, dtype=float)
        elec_kW = merged["elecLoad"].to_numpy(dtype=float)
        T_e = cfg["weather"]["T"].to_numpy(dtype=float)
        Q_sol_win = np.asarray(model.profiles["bQ_sol_Windows"], dtype=float)
        Q_sol_opq = np.asarray(model.profiles["bQ_sol_Opaque"], dtype=float)
        Q_int = merged["Q_ig"].to_numpy(dtype=float) if "Q_ig" in merged else np.zeros(len(times))

        hourly = pd.DataFrame({
            "building_feature_id": np.full(len(times), building_feature_id, dtype=np.int64),
            "time": times,
            "T_e": T_e,
            "T_air": np.asarray(model.T_air, dtype=float),
            "T_m": np.asarray(model.T_m, dtype=float),
            "T_sur": np.asarray(model.T_sur, dtype=float),
            "Q_heat_kW": heat_kW,
            "Q_cool_kW": cool_kW,
            "Q_sol_win_kW": Q_sol_win,
            "Q_sol_opq_kW": Q_sol_opq,
            "Q_int_kW": Q_int,
            "elec_kW": elec_kW,
        })

        row["heating_kWh"] = round(float(heat_kW.sum()), 2)
        row["cooling_kWh"] = round(float(np.abs(cool_kW).sum()), 2)
        row["elec_kWh"] = round(float(elec_kW.sum()), 2)
        row["dhw_kWh"] = round(float(model.dhw_kWh.sum()), 2) if model.dhw_kWh is not None else 0.0
        row["peak_heat_kW"] = round(float(heat_kW.max()), 4)
        row["peak_hour"] = str(times[int(np.argmax(heat_kW))])
        row["heating_hours"] = int(np.sum(heat_kW > 1e-6))
        row["cooling_hours"] = int(np.sum(np.abs(cool_kW) > 1e-6))
        row["deadband_hours"] = int(np.sum((heat_kW <= 1e-6) & (np.abs(cool_kW) <= 1e-6)))
        row["T_air_mean"] = round(float(np.mean(model.T_air)), 2)
        row["status"] = "ok"
        return row, hourly

    except _PER_BUILDING_ERRORS as exc:
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row, None


def _read_id_list(path: str) -> list[int]:
    ids = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                ids.append(int(line))
    return ids


def run_hourly_batch(
    config: BatchConfig,
    building_ids: list[int],
    hourly_output: str | Path,
    summary_output: str | Path,
    flush_every: int = 25,
) -> tuple[Path, Path]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    hourly_output = Path(hourly_output)
    summary_output = Path(summary_output)
    hourly_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.parent.mkdir(parents=True, exist_ok=True)

    source = build_source(config.source_kind, config.source_path)
    u_value_overrides = _load_u_value_overrides(config)
    service_reference = _load_region_table(
        config, "service_building_reference.csv", "non-residential buildings will be skipped",
    )
    measure_overrides = _load_region_table(
        config, "refurbishment_measure_reference.csv",
        "TABULA's own published measure performance will be used unchanged",
    )
    weather_df = _batch_weather(source, config)

    total = len(building_ids)
    logger.info("Hourly batch: %d building id(s)", total)

    hourly_schema = pa.schema([
        ("building_feature_id", pa.int64()),
        ("time", pa.timestamp("ns")),
        ("T_e", pa.float32()), ("T_air", pa.float32()), ("T_m", pa.float32()), ("T_sur", pa.float32()),
        ("Q_heat_kW", pa.float32()), ("Q_cool_kW", pa.float32()),
        ("Q_sol_win_kW", pa.float32()), ("Q_sol_opq_kW", pa.float32()),
        ("Q_int_kW", pa.float32()), ("elec_kW", pa.float32()),
    ])
    summary_schema = pa.schema([
        ("building_feature_id", pa.int64()), ("status", pa.string()), ("error", pa.string()),
        ("building_type", pa.string()), ("construction_period", pa.string()),
        ("A_ref", pa.float64()), ("n_walls", pa.int64()), ("n_exposed", pa.int64()),
        ("residential_units", pa.float64()), ("neighbour_status", pa.string()),
        ("construction_year_class", pa.string()), ("matched_via_label", pa.string()),
        ("refurbishment_variant", pa.string()), ("residential_units_source", pa.string()),
        ("heating_kWh", pa.float64()), ("cooling_kWh", pa.float64()), ("elec_kWh", pa.float64()),
        ("dhw_kWh", pa.float64()), ("peak_heat_kW", pa.float64()), ("peak_hour", pa.string()),
        ("heating_hours", pa.int64()), ("cooling_hours", pa.int64()), ("deadband_hours", pa.int64()),
        ("T_air_mean", pa.float64()),
    ])

    start = time.time()
    n_ok = n_error = 0
    pending_hourly: list[pd.DataFrame] = []
    pending_summary: list[dict[str, Any]] = []

    def _flush(hw: pq.ParquetWriter, sw: pq.ParquetWriter) -> None:
        nonlocal pending_hourly, pending_summary
        if pending_hourly:
            block = pd.concat(pending_hourly, ignore_index=True)
            hw.write_table(pa.Table.from_pandas(block, schema=hourly_schema, preserve_index=False))
            pending_hourly = []
        if pending_summary:
            sw.write_table(pa.Table.from_pylist(pending_summary, schema=summary_schema))
            pending_summary = []

    with pq.ParquetWriter(str(hourly_output), hourly_schema) as hw, \
         pq.ParquetWriter(str(summary_output), summary_schema) as sw, \
         ProcessPoolExecutor(
             max_workers=config.workers,
             initializer=_worker_init,
             initargs=(
                 config.source_kind, str(config.source_path), config.country,
                 weather_df, u_value_overrides, service_reference, measure_overrides,
                 config.region_code,
             ),
         ) as executor:
        future_to_id = {
            executor.submit(_process_one_building_hourly, bid, config.use_milp): bid
            for bid in building_ids
        }
        for completed, future in enumerate(as_completed(future_to_id), start=1):
            bid = future_to_id[future]
            try:
                row, hourly = future.result()
            except _PER_BUILDING_ERRORS as exc:
                row = {col: None for col in _SUMMARY_COLUMNS}
                row["building_feature_id"] = bid
                row["status"] = "error"
                row["error"] = f"worker raised {type(exc).__name__}: {exc}"
                hourly = None

            pending_summary.append(row)
            if hourly is not None:
                pending_hourly.append(hourly)
            if row["status"] == "ok":
                n_ok += 1
            else:
                n_error += 1

            if completed % max(1, flush_every) == 0 or completed == total:
                _flush(hw, sw)
                elapsed = time.time() - start
                logger.info(
                    "%d/%d done (ok=%d error=%d) -- %.1fs elapsed, %.2f buildings/s",
                    completed, total, n_ok, n_error, elapsed,
                    completed / elapsed if elapsed > 0 else 0.0,
                )

    logger.info(
        "Hourly batch complete: %d ok, %d error -- wrote %s and %s",
        n_ok, n_error, hourly_output, summary_output,
    )
    return hourly_output, summary_output


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hourly-retention companion to buem.analysis.batch.")
    parser.add_argument("--data-dir", type=str, required=True, help="CsvBuildingSource region directory.")
    parser.add_argument("--country", type=str, default="NL")
    parser.add_argument("--region-code", type=str, default=None)
    parser.add_argument("--latitude", type=float, default=DEFAULT_LATITUDE)
    parser.add_argument("--longitude", type=float, default=DEFAULT_LONGITUDE)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--provider", type=str, default=DEFAULT_WEATHER_PROVIDER,
                        choices=["merra-2", "cosmo-rea6", "era5-land"])
    parser.add_argument("--building-ids-file", type=str, required=True,
                        help="One building_feature_id per line -- the exact population to re-run.")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--use-milp", action="store_true")
    parser.add_argument("--flush-every", type=int, default=25)
    parser.add_argument("--output", type=str, required=True, help="Hourly long-format Parquet path.")
    parser.add_argument("--summary-output", type=str, required=True, help="Per-building annual summary Parquet path.")
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    args = _build_arg_parser().parse_args(argv)
    config = BatchConfig(
        source_kind="csv",
        data_dir=args.data_dir,
        country=args.country,
        region_code=args.region_code,
        latitude=args.latitude,
        longitude=args.longitude,
        year=args.year,
        provider=args.provider,
        workers=args.workers,
        use_milp=args.use_milp,
    )
    building_ids = _read_id_list(args.building_ids_file)
    run_hourly_batch(config, building_ids, args.output, args.summary_output, flush_every=args.flush_every)


if __name__ == "__main__":
    main()
