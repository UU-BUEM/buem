"""Attach BAG's registered floor area and dwelling count to a Netherlands
region's building table.

Reads the region's ``bag_use_function.csv`` (one row per BAG
verblijfsobject, from ``scripts/fetch_bag_use_functions.py``) and rewrites
``lod2_building_feature.csv`` in place with:

- ``bag_floor_area`` -- summed usable floor area of the building's units in
  active use, which ``LOD2Mapper`` then uses as the reference floor area
  instead of footprint x storeys;
- ``residential_units_bag`` and, for residential buildings, a
  ``residential_units`` taken from BAG's in-use dwelling count
  (``residential_units_source = "bag"``), with RIVM's count and the
  floor-area estimate kept as fallbacks.

    python scripts/refresh_nl_bag_attributes.py [data_dir ...]

Idempotent: re-running reproduces the same table.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

from buem.buildings.datasources.bag_use_function import (
    load_use_functions,
    summarize_use_by_pand,
)
from buem.buildings.datasources.nl_archetype_mapper import (
    apply_bag_floor_area_and_units,
    repair_dwelling_counts,
)

DEFAULT_DATA_DIR = Path("src/buem/data/buildings/netherlands/Loenen")


def refresh(data_dir: Path) -> int:
    path = data_dir / "lod2_building_feature.csv"
    use_path = data_dir / "bag_use_function.csv"
    if not path.exists() or not use_path.exists():
        print(f"ERROR: {path} or {use_path} not found", file=sys.stderr)
        return 1
    before = pd.read_csv(path, na_values=["NULL"], low_memory=False)
    use = summarize_use_by_pand(load_use_functions(use_path))
    after = repair_dwelling_counts(apply_bag_floor_area_and_units(before, use))
    after.to_csv(path, index=False)

    res = after["is_residential"].fillna(False).astype(bool)
    storeys = after["number_of_storeys"].fillna(1.0).clip(lower=1.0)
    lod2 = after["area_total_floor"].fillna(0.0) * storeys
    ratio = (lod2 / after["bag_floor_area"])[res].dropna()
    print(f"Wrote {path}")
    print(f"  residential buildings:               {int(res.sum())}")
    print(f"  with registered floor area:          {int(after.loc[res, 'bag_floor_area'].notna().sum())}")
    print(f"  LOD2 / registered area, median:      {ratio.median():.2f}")
    print(f"  dwellings before (residential):      {before.loc[before['is_residential'].fillna(False).astype(bool), 'residential_units'].sum():.0f}")
    print(f"  dwellings after (residential):       {after.loc[res, 'residential_units'].sum():.0f}")
    print(f"  dwelling-count source:               {after.loc[res, 'residential_units_source'].value_counts().to_dict()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    dirs = [Path(a) for a in args] or [DEFAULT_DATA_DIR]
    return max(refresh(d) for d in dirs)


if __name__ == "__main__":
    raise SystemExit(main())
