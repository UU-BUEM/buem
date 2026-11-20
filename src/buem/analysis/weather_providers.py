"""
Multi-provider weather extraction for analysis/comparison purposes.

Wraps ``buem.config.weather_cache.get_or_fetch_weather()`` per provider
(reusing its existing feather-cache convention -- a repeat call for the
same (provider, lat, lon, year) is a fast local read, not a re-fetch).

Two known local-archive gaps get an explicit, logged, **in-memory-only**
fallback -- neither ever writes to the underlying processed archive, both
only correct a copy held in this process:

- **cosmo-rea6**: ``weather.point_query``'s cosmo-rea6 code path has no
  completeness check of its own (unlike era5-land/merra-2's shared
  ``_get_point_regular_grid``) -- a missing monthly file is silently
  dropped rather than raising, so a genuinely incomplete year can pass
  through unnoticed. Retried via the provider's separate annual export
  file (``COSMO_REA6_{year}.nc`` if present, else
  ``COSMO_REA6_<REGION>_{year}_annual_all_attrs.nc`` — checked directly
  here, since ``weather.point_query``'s own annual-file check only
  recognizes the former exact name) when the monthly path comes back
  short.
- **era5-land**: a month's first hourly GHI/sf stamp can still hold the
  raw, unrepaired month-boundary artifact (see
  ``weather.providers.era5_land.boundary_repair``) -- ``weather
  .point_query`` already detects this and raises rather than returning
  it. The affected stamp is always a calendar month's first hour, i.e.
  genuinely zero solar flux regardless of location -- corrected to 0.0
  here and DNI/DHI reconstructed from the corrected series using the
  same ``reconstruct_dni_dhi`` call ``weather.point_query`` itself uses
  for era5-land (``method="dirint"``), so results stay consistent with
  what a fully-repaired archive would have produced.

See ``.claude/weather_provider_gaps_for_3way_comparison.md`` (buem repo)
for the full incident writeup these fallbacks were built from.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from buem.config.weather_cache import get_or_fetch_weather

logger = logging.getLogger(__name__)

DEFAULT_PROVIDERS: tuple[str, ...] = ("merra-2", "cosmo-rea6", "era5-land")

# Mirrors weather.point_query's own private _OUTPUT_SUBDIR mapping -- kept
# as a small local copy rather than importing that underscore-prefixed
# internal, matching this codebase's existing convention for other
# sibling-package deep paths (see occupancy imports in attribute_builder.py).
_OUTPUT_SUBDIR: dict[str, str] = {
    "cosmo-rea6": "cosmo_rea6",
    "era5-land": "era5_land",
    "merra-2": "merra2",
}

_REQUIRED_COLUMNS = ("T", "GHI", "DHI", "DNI")


def _expected_hours(year: int) -> int:
    """Hours in *year* (8760, or 8784 for a leap year)."""
    start = pd.Timestamp(f"{year}-01-01")
    end = pd.Timestamp(f"{year + 1}-01-01")
    return int((end - start) / pd.Timedelta(hours=1))


def _archive_output_dir(provider: str) -> Path:
    """Resolve a provider's processed-archive output directory the same
    way ``get_point_weather(..., data_dir=None)`` does internally:
    ``weather``'s own ``data_root()`` (reads ``WEATHER_DATA_DIR``) plus
    the provider's output subdirectory.

    Deliberately does *not* also honor ``BUEM_WEATHER_DATA_DIR`` here --
    that variable is passed straight through as ``get_point_weather``'s
    ``data_dir`` argument elsewhere in this codebase (``weather_cache.py``),
    which uses it as an already-provider-specific path with **no**
    subdirectory appended (see ``weather.point_query._output_dir``) --
    an existing, documented single-provider-only caveat (``.env.example``).
    Duplicating that ambiguous convention here would silently pick the
    wrong directory whenever ``BUEM_WEATHER_DATA_DIR`` is set to a
    provider-specific path rather than a shared multi-provider root.
    """
    from weather.common.env import data_root  # lazy: only needed on this fallback path

    return data_root() / _OUTPUT_SUBDIR[provider] / "output"


def _cosmo_annual_fallback(latitude: float, longitude: float, year: int) -> pd.DataFrame:
    """Read cosmo-rea6's separate annual export file directly, bypassing
    the monthly-file glob that a missing month silently shrinks."""
    import xarray as xr
    from weather.common.geo_lookup import find_nearest_cell

    out_dir = _archive_output_dir("cosmo-rea6")
    candidates = sorted(out_dir.glob(f"COSMO_REA6_{year}.nc")) or sorted(
        out_dir.glob(f"COSMO_REA6_*_{year}_annual_all_attrs.nc")
    )
    if not candidates:
        raise FileNotFoundError(
            f"cosmo-rea6 {year}: no annual export file under {out_dir} "
            f"either (looked for COSMO_REA6_{year}.nc and "
            f"COSMO_REA6_*_{year}_annual_all_attrs.nc)."
        )
    with xr.open_dataset(str(candidates[0])) as ds:
        iy, ix = find_nearest_cell(ds, latitude, longitude)
        cell = ds.isel(y=iy, x=ix)
        df = pd.DataFrame({col: cell[col].to_series() for col in _REQUIRED_COLUMNS})
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    df.index = idx
    return df[list(_REQUIRED_COLUMNS)]


def _era5_land_boundary_fix(latitude: float, longitude: float, year: int) -> pd.DataFrame:
    """Rebuild a complete era5-land year, zeroing any unrepaired month's
    first-hour GHI artifact rather than refusing the whole year."""
    import xarray as xr
    from weather.common.dni_reconstruction import reconstruct_dni_dhi

    out_dir = _archive_output_dir("era5-land")
    paths = sorted(out_dir.glob(f"ERA5_LAND_{year}_??_all_attrs.nc"))
    if not paths:
        raise FileNotFoundError(f"era5-land {year}: no monthly files under {out_dir}.")

    ghi_parts: list[pd.Series] = []
    t_parts: list[pd.Series] = []
    ps_parts: list[pd.Series] = []
    cell_lat = cell_lon = None
    for p in paths:
        with xr.open_dataset(str(p)) as raw:
            renames = {}
            if "T" not in raw.variables and "t2m" in raw.variables:
                renames["t2m"] = "T"
            if "PS" not in raw.variables and "sp" in raw.variables:
                renames["sp"] = "PS"
            ds = raw.rename(renames) if renames else raw

            cell = ds.sel(latitude=latitude, longitude=longitude, method="nearest")
            ghi = cell["GHI"].to_series()
            t = cell["T"].to_series()

            status = str(ds.attrs.get("boundary_status", ""))
            if status and not status.startswith("BOUNDARY_REPAIRED"):
                bad_val = float(ghi.iloc[0])
                ghi.iloc[0] = 0.0
                logger.warning(
                    "era5-land %s: unrepaired month-boundary stamp at %s "
                    "(raw value %.1f) -- corrected to 0.0 (genuinely zero "
                    "solar flux at a calendar month's first hour).",
                    p.name, ghi.index[0], bad_val,
                )

            ghi_parts.append(ghi)
            t_parts.append(t)
            if "PS" in cell:
                ps_parts.append(cell["PS"].to_series())
            cell_lat, cell_lon = float(cell["latitude"]), float(cell["longitude"])

    ghi = pd.concat(ghi_parts).sort_index()
    t = pd.concat(t_parts).sort_index()
    pressure = pd.concat(ps_parts).sort_index() if ps_parts else None

    dni_dhi = reconstruct_dni_dhi(ghi, cell_lat, cell_lon, method="dirint", pressure=pressure)
    dni, dhi = dni_dhi["DNI"], dni_dhi["DHI"]
    for s in (dni, dhi):
        idx = pd.DatetimeIndex(s.index)
        if idx.tz is not None:
            s.index = idx.tz_convert(None)

    df = pd.DataFrame({"T": t, "GHI": ghi, "DNI": dni, "DHI": dhi})
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    df.index = idx
    return df[list(_REQUIRED_COLUMNS)]


def _extract_one_provider(latitude: float, longitude: float, year: int, provider: str) -> pd.DataFrame:
    if provider == "era5-land":
        try:
            return get_or_fetch_weather(latitude, longitude, year, provider)
        except RuntimeError as exc:
            if "unrepaired month" not in str(exc):
                raise
            logger.warning(
                "era5-land %d at (%.4f, %.4f): %s -- applying in-memory "
                "boundary-hour fix.", year, latitude, longitude, exc,
            )
            return _era5_land_boundary_fix(latitude, longitude, year)

    if provider == "cosmo-rea6":
        df = get_or_fetch_weather(latitude, longitude, year, provider)
        expected = _expected_hours(year)
        if len(df) < expected:
            logger.warning(
                "cosmo-rea6 %d at (%.4f, %.4f): monthly archive incomplete "
                "(%d/%d hours) -- retrying via the annual export file.",
                year, latitude, longitude, len(df), expected,
            )
            df = _cosmo_annual_fallback(latitude, longitude, year)
            if len(df) < expected:
                raise FileNotFoundError(
                    f"cosmo-rea6 {year}: still incomplete ({len(df)}/{expected} "
                    "hours) even via the annual export file -- no complete "
                    "local archive for this year/location."
                )
        df = _fix_cosmo_hour_labels(df)
        # The hour-ending archive carries a stamp at 1 January 00:00 that
        # covers the last hour of the previous year; after the shift to
        # hour-beginning labels it falls in that year, so keep the
        # calendar year only (the annual export file includes it, which
        # left 8,761 rows whenever the fallback was taken).
        return df[df.index.year == year]

    return get_or_fetch_weather(latitude, longitude, year, provider)


def _fix_cosmo_hour_labels(df: pd.DataFrame) -> pd.DataFrame:
    """cosmo-rea6's raw index uses hour-ending labels (its "01:00" stamp
    covers 00:00-01:00), unlike occupancy's/the other providers' hour-
    beginning convention -- shift back 1h so every provider (and the
    occupancy profile any caller aligns this against) shares the same
    labeling. Without this, AttributeBuilder._reindex_or_raise() fails at
    exactly the year's last hour (the one stamp with no valid match within
    its 30-minute tolerance) -- confirmed the hard way building this
    module's precursor comparison."""
    df = df.copy()
    df.index = df.index - pd.Timedelta(hours=1)
    return df


def extract_provider_weather(
    latitude: float,
    longitude: float,
    year: int,
    providers: tuple[str, ...] | list[str] = DEFAULT_PROVIDERS,
) -> dict[str, pd.DataFrame]:
    """Fetch a clean, complete hourly T/GHI/DHI/DNI DataFrame per provider
    for one location/year.

    Each returned DataFrame has exactly ``_expected_hours(year)`` rows and
    no NaNs -- an incomplete or corrupted archive raises rather than
    silently returning a partial/wrong series, matching the rest of this
    codebase's "never silently model the wrong building" convention. See
    the module docstring for the two known fallbacks this applies.

    Parameters
    ----------
    latitude, longitude : float
        Target location (WGS84).
    year : int
        Calendar year.
    providers : sequence of str
        Providers to fetch, default ``("merra-2", "cosmo-rea6", "era5-land")``.

    Returns
    -------
    dict[str, pandas.DataFrame]
        provider -> DataFrame (DatetimeIndex, columns T/GHI/DHI/DNI).
    """
    results: dict[str, pd.DataFrame] = {}
    expected = _expected_hours(year)
    for provider in providers:
        df = _extract_one_provider(latitude, longitude, year, provider)
        if len(df) != expected:
            raise ValueError(
                f"{provider} {year}: expected {expected} hourly rows, got {len(df)}."
            )
        if df.isna().any().any():
            raise ValueError(f"{provider} {year}: result contains NaN values.")
        results[provider] = df
    return results


__all__ = ["DEFAULT_PROVIDERS", "extract_provider_weather"]
