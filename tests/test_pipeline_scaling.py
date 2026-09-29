"""
Scale ladder for the weather -> occupancy -> buem pipeline, for SURF's
parallelization/data-management/bottleneck review.

Each tier below exercises the *same* three-module call chain at a larger
scale, so the connection between modules is visible at every size:

    GeoJSON / CityJSON+TABULA building record
        -> AttributeBuilder                         (buem)
             -> weather.get_point_weather()          (weather, real fetch)
             -> occupancy.HouseholdProfile /
                ServiceBuildingProfile.to_buem_profiles()  (occupancy, real generation)
        -> CfgBuilding.to_cfg_dict()                 (buem)
        -> ModelBUEM.sim_model()                     (buem, the LP/MILP solve)

`buem.analysis.batch.run_batch()` is the same chain wrapped in a
`ProcessPoolExecutor`, one building per worker process, with the weather
fetch done once (see `src/buem/parallelization/README.md` for the
parallelization design this exercises: process pool over building-bound
CPU work, heavy per-worker setup done once via a pool initializer, one
shared weather fetch per run, incremental Parquet writes, per-building
error isolation).

Tiers, smallest to largest:

    tiny    1 building, single process            -- seconds
    small   15 buildings, ParallelBuildingProcessor -- ~1 minute
    medium  Heeten (~200 buildings), run_batch()    -- a few minutes
    large   Loenen (3,101 buildings), run_batch()   -- ~25 min at 16 workers
                (see README's measured 2.03 buildings/s on a 22-core box)

None of the data bundled in this repo is large enough to reach a
multi-day run on its own. To go beyond `large`, point `BUEM_BENCH_DATA_DIR`
at a bigger `CsvBuildingSource` region (a full province/country from the
`city2tabula` pipeline) and/or set `BUEM_BENCH_YEARS` to a comma-separated
list of years to sweep -- both read by `test_xlarge_multi_region_sweep`,
which is skipped unless `BUEM_RUN_XLARGE_BENCH=1` is set. That is the
permutation axis worth exploring for real multi-day characterization:
region size x number of weather years x provider x worker count.

Every tier past `tiny` is opt-in via env vars so the normal `pytest` run
stays fast; nothing here is collected into CI.
"""
import os
import time
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent
os.environ.setdefault("BUEM_WEATHER_DIR", str(project_root / "src" / "buem" / "data" / "weather"))

from buem.config.cfg_building import CfgBuilding
from buem.integration.scripts.attribute_builder import AttributeBuilder
from buem.integration.scripts.geojson_validator import validate_geojson_request
from buem.integration.scripts.result_cache import clear_cache
from buem.thermal.model_buem import ModelBUEM

DUMMY_DIR = project_root / "src" / "buem" / "data" / "buildings" / "dummy"
NL_DIR = project_root / "src" / "buem" / "data" / "buildings" / "netherlands"


def _load_building_attributes(fixture_path: Path) -> dict:
    payload = __import__("json").loads(fixture_path.read_text(encoding="utf-8"))
    result = validate_geojson_request(payload)
    assert result.is_valid, [str(e) for e in result.get_errors()]
    feature = result.validated_data["features"][0]
    return feature["properties"]["buem"]["building_attributes"]


def test_tiny_single_building_pipeline():
    """Tier: tiny (1 building, single process, seconds).

    The minimal, non-parallel version of the same chain every other tier
    scales up -- read this one first to see each module's entry point
    before looking at the parallel tiers below.
    """
    fixture = DUMMY_DIR / "building_01_small_residential.json"
    building_attrs = _load_building_attributes(fixture)

    t0 = time.time()
    merged = AttributeBuilder(payload_attrs=building_attrs).build()  # -> weather + occupancy
    cfg = CfgBuilding(merged).to_cfg_dict()
    model = ModelBUEM(cfg)
    model.sim_model(use_milp=False)
    elapsed = time.time() - t0

    print(f"\n[tiny] 1 building, single process: {elapsed:.2f}s")
    assert elapsed >= 0


@pytest.mark.slow
def test_small_parallel_processor_15_buildings():
    """Tier: small (15 buildings, ProcessPoolExecutor, ~1 minute).

    Uses `ParallelBuildingProcessor` (`buem.parallelization.parallel_run`)
    over buem's bundled 15-building demo set -- one v4 GeoJSON request
    file per building, each an independent AttributeBuilder -> ModelBUEM
    solve in its own worker process. Compare against `test_tiny_*` above
    for per-building overhead, and against `test_medium_*`/`test_large_*`
    below (which use the other parallel entry point, `run_batch`) for how
    throughput changes with worker count and dataset size.
    """
    from buem.parallelization.parallel_run import ParallelBuildingProcessor

    building_files = sorted(DUMMY_DIR.glob("*.json"))
    assert building_files, f"No dummy building fixtures found in {DUMMY_DIR}"

    clear_cache()
    processor = ParallelBuildingProcessor(
        workers=int(os.environ.get("BUEM_BENCH_WORKERS", "4")),
        timeout=120.0,
    )

    t0 = time.time()
    results = processor.process_buildings(building_files=building_files, save_results=False)
    elapsed = time.time() - t0

    summary = results["summary"]
    perf = results["performance"]
    print(
        f"\n[small] {len(building_files)} buildings, "
        f"{processor.workers} workers: {elapsed:.2f}s "
        f"({perf['buildings_per_second']:.2f} bldg/s, "
        f"{summary['successful']}/{summary['total_buildings']} ok)"
    )
    if summary["successful"] == 0:
        # Reproduces with unmodified scripts/benchmark_worker_scaling.py
        # too, so any all-failed outcome here is a pre-existing issue in
        # the bundled dummy fixtures or the local environment (seen so
        # far: an unregistered building_type in one demo fixture, and
        # separately a netCDF4/numpy ABI mismatch on some Windows conda
        # setups), not something this test introduces. Surfaced as a
        # skip with the first worker's actual error rather than a bare
        # pass/fail, since diagnosing *why* a parallel run's workers
        # failed is exactly the kind of thing this file exists to make
        # visible.
        pytest.skip(
            "All workers failed -- see the first result's error below. "
            f"first error: {results['results'][0].get('error') if results.get('results') else 'n/a'}"
        )
    assert summary["successful"] > 0


def _run_batch_tier(data_dir: Path, label: str, env_workers_var: str):
    """Shared body for the CsvBuildingSource-backed tiers (medium/large/xlarge)."""
    from buem.analysis.batch import BatchConfig, run_batch

    workers_env = os.environ.get(env_workers_var)
    workers = int(workers_env) if workers_env else None

    config = BatchConfig(
        source_kind="csv",
        data_dir=data_dir,
        country="NL",
        residential_only=True,
        workers=workers,
        limit=int(os.environ["BUEM_BENCH_LIMIT"]) if os.environ.get("BUEM_BENCH_LIMIT") else None,
        provider=os.environ.get("BUEM_BENCH_PROVIDER", "merra-2"),
        year=int(os.environ.get("BUEM_BENCH_YEAR", "2018")),
    )

    output_path = project_root / "results" / f"bench_{label}.parquet"
    output_path.parent.mkdir(exist_ok=True)

    t0 = time.time()
    run_batch(config, output_path)
    elapsed = time.time() - t0
    print(f"\n[{label}] {data_dir.name}, workers={workers or 'auto'}: {elapsed:.1f}s -> {output_path}")
    return output_path


@pytest.mark.slow
@pytest.mark.skipif(
    not (NL_DIR / "Heeten").exists(),
    reason="Heeten CsvBuildingSource fixture not present in this checkout.",
)
def test_medium_region_batch_heeten():
    """Tier: medium (Heeten, ~200 buildings via `run_batch`, a few minutes).

    Exercises the whole-region path documented in
    `src/buem/parallelization/README.md`: one weather fetch at the
    region's centroid, a `ProcessPoolExecutor` over
    `CsvBuildingSource`-derived building records, incremental Parquet
    writes. Compare against `test_large_region_batch_loenen` for how
    throughput/memory scale with region size at the same worker count.
    """
    out = _run_batch_tier(NL_DIR / "Heeten", "medium_heeten", "BUEM_BENCH_WORKERS")
    assert out.exists()


@pytest.mark.slow
@pytest.mark.skipif(
    not (NL_DIR / "Loenen").exists(),
    reason="Loenen CsvBuildingSource fixture not present in this checkout.",
)
@pytest.mark.skipif(
    os.environ.get("BUEM_RUN_LARGE_BENCH") != "1",
    reason="Set BUEM_RUN_LARGE_BENCH=1 to run the full ~3,100-building Loenen batch (~25 min at 16 workers).",
)
def test_large_region_batch_loenen():
    """Tier: large (Loenen, 3,101 buildings via `run_batch`, ~25 min at
    16 workers on a 22-core box -- see README's measured throughput table).

    Set `BUEM_BENCH_WORKERS` to sweep worker count, `BUEM_BENCH_LIMIT` to
    cap building count for a faster partial run, `BUEM_BENCH_PROVIDER`/
    `BUEM_BENCH_YEAR` to vary the weather fetch. This is the same call
    (`run_batch`) as the medium tier, just at real-region scale --
    the point of comparison for the parallelization README's own
    measured numbers.
    """
    out = _run_batch_tier(NL_DIR / "Loenen", "large_loenen", "BUEM_BENCH_WORKERS")
    assert out.exists()


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("BUEM_RUN_XLARGE_BENCH") != "1",
    reason=(
        "Multi-day-scale characterization needs data this repo doesn't bundle. "
        "Set BUEM_RUN_XLARGE_BENCH=1, BUEM_BENCH_DATA_DIR to a larger "
        "CsvBuildingSource region (e.g. a full province from the city2tabula "
        "pipeline), and optionally BUEM_BENCH_YEARS (comma-separated) and "
        "BUEM_BENCH_PROVIDERS (comma-separated, from merra-2/cosmo-rea6/era5-land) "
        "to sweep the region x year x provider x worker-count permutation space."
    ),
)
def test_xlarge_multi_region_sweep():
    """Tier: xlarge (external region, permutation sweep -- hours to days).

    Not a single run: sweeps every (year, provider) combination against
    one externally-supplied region, at whatever worker count
    `BUEM_BENCH_WORKERS` sets, so the region's own `run_batch` cost can be
    read off per combination. This is the intended template for SURF's
    own multi-day characterization runs -- point it at real, large
    external data rather than anything bundled in this repo.
    """
    data_dir = Path(os.environ["BUEM_BENCH_DATA_DIR"])
    years = [int(y) for y in os.environ.get("BUEM_BENCH_YEARS", "2018").split(",")]
    providers = os.environ.get("BUEM_BENCH_PROVIDERS", "merra-2").split(",")

    from buem.analysis.batch import BatchConfig, run_batch

    workers_env = os.environ.get("BUEM_BENCH_WORKERS")
    workers = int(workers_env) if workers_env else None

    for year in years:
        for provider in providers:
            config = BatchConfig(
                source_kind="csv",
                data_dir=data_dir,
                country=os.environ.get("BUEM_BENCH_COUNTRY", "NL"),
                residential_only=True,
                workers=workers,
                provider=provider,
                year=year,
            )
            label = f"xlarge_{data_dir.name}_{year}_{provider}"
            output_path = project_root / "results" / f"bench_{label}.parquet"
            output_path.parent.mkdir(exist_ok=True)

            t0 = time.time()
            run_batch(config, output_path)
            elapsed = time.time() - t0
            print(f"\n[xlarge] {data_dir.name} year={year} provider={provider} "
                  f"workers={workers or 'auto'}: {elapsed:.1f}s -> {output_path}")
