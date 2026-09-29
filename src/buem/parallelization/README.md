# Multi-building processing

Two separate parallel paths exist, and they consume different inputs.
Pick by what you have.

| You have | Use | Entry point |
|---|---|---|
| A **building-source directory** (CityJSON-derived CSVs, or the TABULA workbook) | `buem.analysis.batch` | `python -m buem.analysis.batch` |
| **One v4 GeoJSON request file per building** | `ParallelBuildingProcessor` (this package) | `buem multibuilding` |

For a whole community — every building of a region, straight from the
data the Netherlands pipeline produces — **use `buem.analysis.batch`**.
This package cannot read a building source; it only takes pre-written
request files, and its `buem multibuilding` CLI is wired to a fixed set of
15 demo JSONs under `src/buem/data/buildings/dummy/`.

`tests/test_pipeline_scaling.py` exercises both entry points as a graduated
scale ladder (1 building -> 15 -> a real region -> an externally-supplied
larger region), for characterizing where time actually goes across the
weather -> occupancy -> buem chain this package sits in the middle of.

---

## Whole-region runs — `buem.analysis.batch`

```bash
python -m buem.analysis.batch --source csv \
    --data-dir src/buem/data/buildings/netherlands/Loenen \
    --country NL --residential-only \
    --workers 16 --resume \
    --output results/loenen.parquet
```

Aggregate the result against CBS without re-simulating:

```bash
python -m buem.analysis.netherlands.validation \
    --from-parquet results/loenen.parquet --region-code GM0200
```

On Linux, `scripts/run_region_batch.sh` wraps the same command: it checks
`WEATHER_DATA_DIR`, pins BLAS to one thread per worker, sizes `--workers`
to the host, and detaches under `nohup`.

### Design

- **`ProcessPoolExecutor`**, one building per task. The work is
  CPU-bound and independent per building, so processes (not threads)
  are what buy anything — the GIL would serialise a thread pool.
- **Heavy setup once per worker, not once per task.** `_worker_init`
  builds the `LOD2Mapper` (which opens a multi-MB source) a single time
  per process and receives the shared weather DataFrame through the pool
  initializer. Passing weather per task instead would re-pickle it
  thousands of times across a full region.
- **One weather fetch for the whole run**, placed at the region's own
  mean centroid. A village spans a few kilometres, comfortably inside one
  reanalysis grid cell, so per-building fetches would return the same
  series at far greater cost.
- **Incremental Parquet writes** every `--flush-every` buildings, so an
  interrupted run keeps its progress.
- **`--resume`** skips ids already in the output and carries their rows
  forward. Safe to pass always.
- **Per-building error isolation.** A building that fails is recorded as
  an `error` row with its exception; it never aborts the run.

### Measured performance

Real Loenen run, all 3,101 residential buildings, 16 workers on a
22-logical-core machine (Intel Ultra 7 165H):

| Metric | Value |
|---|---|
| Throughput | **2.03 buildings/s** |
| Wall time | **25.5 min** |
| Outcome | 3,101 ok, 0 skipped, 0 errors |
| Memory | ~300 MB per worker (~5 GB total) |

Per-building cost is dominated by the LP solve — 4 × 8760 = 35,040
variables, CLARABEL with an OSQP fallback — and varies little between
buildings, so **throughput scales with worker count** and a whole
community is a laptop-scale job. Scaling measured on the same box:

| Workers | Throughput |
|---|---|
| 8 | ~1.1 buildings/s |
| 16 | ~2.0 buildings/s |

Two knobs matter on a bigger host:

- **Leave a core or two free.** The parent process collects results and
  writes Parquet; starving it slows everything.
- **Pin BLAS to one thread per worker**
  (`OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`).
  The per-building linear algebra is small, and letting each worker spawn
  its own thread pool oversubscribes the cores.

---

## Request-file runs — `ParallelBuildingProcessor`

For a list of already-written v4 GeoJSON request files (the shape the
REST API accepts), rather than a building source.

```python
from buem.parallelization.parallel_run import ParallelBuildingProcessor

processor = ParallelBuildingProcessor(workers=4, timeout=300)
results = processor.process_buildings(building_files=[...], save_results=True)
print(results["summary"]["success_rate_percent"])
```

Defaults to `min(16, max(2, cpu_count() * 0.6))` workers. Like
`batch.py` it pre-imports the heavy modules per worker and pre-warms the
weather cache for every distinct `(lat, lon, year, provider)` in the
batch before forking.

Sibling modules: `sequence_run.py` (serial, for debugging a single
problematic building with full logging), `performance_comparison.py`
(parallel-vs-serial benchmarking), `analyze_multibuilding.py`,
`production_optimize.py`.

### CLI

```bash
buem multibuilding --validate-system          # print cores/RAM and a suggested --workers
buem multibuilding --test parallel --workers 10
buem multibuilding --test comparison          # parallel vs sequential
buem multibuilding --test optimize            # sweep worker counts
```

These run the 15 bundled demo buildings only — there is no `--input-dir`.
They are a benchmark harness, not a way to process your own data.

---

## Troubleshooting

**Out of memory** — reduce `--workers`; budget ~300–500 MB each.

**A few buildings error** — read the `error` column of the output
Parquet; each row carries its own exception. Re-run just those with
`--building-ids`.

**Run died partway** — re-run the identical command with `--resume`.

**Slower than expected on a many-core host** — check the BLAS thread
variables above; unpinned, they are the usual cause.

**Every worker fails immediately, `ParallelBuildingProcessor` only** — on
some Windows conda environments, a `netCDF4`/`numpy` ABI mismatch
(`RuntimeWarning: numpy.ndarray size changed, may indicate binary
incompatibility`) fires on `netCDF4`'s first import inside each freshly
spawned worker process, aborting its weather fetch before any building
solves. Not something to work around in code that calls this package —
it is an environment/build issue in the worker's own fresh interpreter,
reproducible with `scripts/benchmark_worker_scaling.py` alone. Rebuild
the conda env's `netCDF4`/`numpy` pair from matching wheels if it shows up.
