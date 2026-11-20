# BuEM — Building Energy Model

[![CI](https://github.com/UU-BUEM/buem/actions/workflows/ci.yml/badge.svg)](https://github.com/UU-BUEM/buem/actions/workflows/ci.yml)
[![Documentation](https://readthedocs.org/projects/buem/badge/?version=latest)](https://buem.readthedocs.io/en/latest/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/downloads/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24392362.svg)](https://doi.org/10.5281/zenodo.24392362)

BuEM computes **hourly heating and cooling demand** for buildings using the
**ISO 13790 5R1C** thermal-network method combined with a linear-programming
solver. It exposes a REST API that accepts GeoJSON and returns energy profiles,
making it straightforward to integrate with urban energy-system models.

**Full documentation:** <https://buem.readthedocs.io/en/latest/>

---

## Quick Start

```bash
# 1. Clone and create the environment
git clone https://github.com/UU-BUEM/buem.git
cd buem
conda env create -f infrastructure/env/buem_env.yml
conda activate buem_env
conda develop src

# (Windows) equivalent one-liner: .\setup.ps1 install  /  setup.bat install

# 2. Point BuEM at real weather data (required — see below)
export WEATHER_DATA_DIR=/path/to/processed/provider/archives

# 3. Verify the installation
buem validate

# 4. Run the thermal model
buem run

# 5. Start the API server (includes Swagger UI)
buem api
# Open http://localhost:5000/api/docs
```

### Weather data is required

Every temperature and irradiance value comes from a real
[`weather`](https://github.com/UU-BUEM/weather) fetch — there is no
bundled dataset and no synthetic fallback, so **BuEM will not import
without one**. Either:

- set `WEATHER_DATA_DIR` (or `BUEM_WEATHER_DATA_DIR`) to a directory of
  processed provider archives, produced by `weather run --provider ...`; or
- set `WEATHER_API_URL` (plus `WEATHER_API_KEY`) to use the `weather`
  repo's point-query HTTP API instead of local files.

`buem validate` checks whichever of the two you configured and fails if
neither is reachable.

`infrastructure/env/buem_env.yml` and `pyproject.toml` pin `occupancy` and
`weather` to released tags (currently `occupancy` v6.1.2, `weather` v1.10.2),
so each BuEM release runs against fixed versions of both. Re-run
`.\setup.ps1 env-update` / `setup.bat env-update` (or `conda env update -n
buem_env -f infrastructure/env/buem_env.yml --prune`) after a `git pull`
that changes those pins.

> For detailed installation instructions (conda and Docker),
> see the [Installation Guide](https://buem.readthedocs.io/en/latest/installation/index.html).

---

## CLI Reference

```bash
buem <command> [options]
```

| Command | Description |
|---|---|
| `buem run [--plot] [--milp]` | Run the thermal model for a single building |
| `buem api [--dev] [--port N]` | Start the REST API server (Gunicorn / Flask) |
| `buem validate` | Verify the installation and environment |
| `buem version` | Print the installed BuEM version |
| `buem multibuilding [--test MODE] [--workers N]` | Benchmark harness on the bundled demo buildings |

```bash
buem --help            # Show all commands
buem <command> --help  # Show options for a specific command
```

> **Note:** `weather` and `occupancy` live in their own repositories within the
> [UU-BUEM](https://github.com/UU-BUEM) organisation, but they are **required
> dependencies, not optional extras** — `pip install buem` pulls both, at
> their pinned release tags, automatically. Every irradiance/temperature value comes from
> [`weather`](https://github.com/UU-BUEM/weather) and every internal-gain and
> electricity profile from [`occupancy`](https://github.com/UU-BUEM/occupancy);
> neither has a fallback.
>
> Full CLI options and examples:
> [Modules → Integration](https://buem.readthedocs.io/en/latest/modules/integration.html)

---

## Running a whole region

`buem run` simulates one building. To run every building of a
neighbourhood or town, use the batch runner, which spreads the work
across processes and writes one row per building to Parquet.

```bash
# Simulate every residential building in a region (~25 min for 3,100
# buildings on 16 cores). --resume is safe to pass always: it skips ids
# already in the output, so an interrupted run continues.
python -m buem.analysis.batch --source csv \
    --data-dir src/buem/data/buildings/netherlands/Loenen \
    --country NL --residential-only \
    --workers 16 --resume \
    --output results/loenen.parquet

# Compare the result against real CBS gas statistics — no re-simulation
python -m buem.analysis.netherlands.validation \
    --from-parquet results/loenen.parquet --region-code GM0200
```

Defaults: weather provider `merra-2`, year `2018`, and the CBS reference
period derived from the weather year so the two cannot drift apart. The
weather point is the region's own mean centroid, fetched once and shared
by every worker. On Linux, `scripts/run_region_batch.sh` wraps this with
host-appropriate worker sizing and `nohup`.

| Guide | Link |
|---|---|
| Pipeline, run options and defaults | [Netherlands Data Pipeline](https://buem.readthedocs.io/en/latest/modules/netherlands.html) |
| Parallelisation design and measured throughput | [`src/buem/parallelization/README.md`](src/buem/parallelization/README.md) |
| Latest validation results vs. CBS | [Validation Results](https://buem.readthedocs.io/en/latest/validation/loenen_cbs.html) |

---

## API Server

BuEM includes a Flask-based HTTP API with interactive **Swagger UI**
documentation.

```bash
buem api          # Start on http://localhost:5000
buem api --dev    # Flask development server
```

| Endpoint | Method | Description |
|---|---|---|
| `/api/process` | POST | Process GeoJSON FeatureCollection (batch) |
| `/api/run` | POST | Run model for a single building config |
| `/api/files/<filename>` | GET | Download result file |
| `/api/health` | GET | Health check |
| `/api/docs` | GET | **Swagger UI** (interactive API browser) |
| `/api/openapi.yaml` | GET | OpenAPI 3.1 specification |

> Full API reference:
> [API Integration](https://buem.readthedocs.io/en/latest/api_integration/index.html)

---

## Docker

```bash
docker compose -f infrastructure/container/docker-compose.yml up    # Start the API in a container
docker compose -f infrastructure/container/docker-compose.yml down  # Stop and remove containers
# or, equivalently: .\setup.ps1 docker-up / docker-down  (setup.bat on Windows CMD)
```

> Docker configuration details:
> [Deployment](https://buem.readthedocs.io/en/latest/deployment/index.html)

---

## Documentation

| Section | Link |
|---|---|
| Introduction & theory | [Introduction](https://buem.readthedocs.io/en/latest/introduction/index.html) |
| Installation (conda, Docker) | [Installation](https://buem.readthedocs.io/en/latest/installation/index.html) |
| Module reference | [Modules](https://buem.readthedocs.io/en/latest/modules/index.html) |
| Validation results | [Validation](https://buem.readthedocs.io/en/latest/validation/index.html) |
| API integration & schemas | [API Integration](https://buem.readthedocs.io/en/latest/api_integration/index.html) |
| Deployment & production | [Deployment](https://buem.readthedocs.io/en/latest/deployment/index.html) |

---

## Publications

<!-- Add publications here, e.g.:
- Sahoo, S. et al. (2026). *Title*. Journal, Volume(Issue), Pages. doi:...
-->

*Publication list will be added here.*

---

## Acknowledgements

BuEM is developed at **Utrecht University** as part of the
[CETP](https://www.nwo.nl/en/programmes/complementary-energy-transition-policies)
programme, funded by the **NWO** (Dutch Research Council).

Building typology data is derived from the
[TABULA/EPISCOPE](https://episcope.eu/welcome/) project
(IEE TABULA — Typology Approach for Building Stock Energy Assessment).

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file
for details.

---

## Requirements

- Python ≥ 3.14
- Key dependencies: cvxpy, flask, numpy, pandas, pvlib, scipy

> Full dependency list:
> [Installation → Prerequisites](https://buem.readthedocs.io/en/latest/installation/index.html)
