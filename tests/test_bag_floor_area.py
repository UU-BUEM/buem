"""
Tests for BAG's registered floor area and dwelling count
(``nl_archetype_mapper.apply_bag_floor_area_and_units``) and for
``LOD2Mapper`` preferring that area over footprint x storeys.
"""
from __future__ import annotations

import math

import pandas as pd

from buem.buildings.datasources.bag_use_function import (
    PandUseSummary,
    summarize_use_by_pand,
)
from buem.buildings.datasources.nl_archetype_mapper import (
    apply_bag_floor_area_and_units,
    repair_dwelling_counts,
)


def _summary(n_res: int, area: float, n_non_res: int = 0) -> PandUseSummary:
    return PandUseSummary(
        n_residential_units=n_res, n_non_residential_units=n_non_res,
        function_counts={}, non_residential_area_m2=0.0, total_area_m2=area,
    )


def _buildings() -> pd.DataFrame:
    return pd.DataFrame({
        "bag_pand_id": ["A", "B", "C", "D"],
        "is_residential": [True, True, True, False],
        "building_type": ["TH", "AB", "SFH", None],
        "footprint_area": [60.0, 400.0, 200.0, 300.0],
        "area_total_floor": [60.0, 400.0, 200.0, 300.0],
        "number_of_storeys": [3, 4, 2, 1],
        "residential_units": [1.0, 3.0, 1.0, 1.0],
        "residential_units_recorded": [1.0, 3.0, 1.0, 1.0],
        "residential_units_source": ["rivm"] * 4,
    })


def test_total_area_sums_every_in_use_unit_whatever_its_function():
    use = pd.DataFrame({
        "bag_pand_id": ["P", "P", "P"],
        "gebruiksdoel": ["woonfunctie", "woonfunctie", "winkelfunctie"],
        "oppervlakte": [80.0, 70.0, 50.0],
        "status": ["Verblijfsobject in gebruik"] * 3,
    })
    s = summarize_use_by_pand(use)["P"]
    assert s.total_area_m2 == 200.0
    assert s.n_residential_units == 2


def test_registered_area_and_dwellings_replace_the_geometric_estimates():
    use = {"A": _summary(1, 110.0), "B": _summary(12, 900.0)}
    out = apply_bag_floor_area_and_units(_buildings(), use)
    assert out.loc[0, "bag_floor_area"] == 110.0
    assert out.loc[1, "bag_floor_area"] == 900.0
    assert out.loc[1, "residential_units"] == 12.0
    assert out.loc[1, "residential_units_source"] == "bag"
    # The RIVM value stays on record, untouched.
    assert out.loc[1, "residential_units_recorded"] == 3.0


def test_buildings_without_a_register_record_keep_their_values():
    out = apply_bag_floor_area_and_units(_buildings(), {"A": _summary(1, 110.0)})
    assert math.isnan(out.loc[2, "bag_floor_area"])
    assert out.loc[2, "residential_units"] == 1.0
    assert out.loc[2, "residential_units_source"] == "rivm"


def test_implausibly_small_registered_area_is_not_used():
    # 40 m2 registered under a 200 m2 footprint: units are missing from
    # the register, so the geometric estimate stays in charge.
    out = apply_bag_floor_area_and_units(_buildings(), {"C": _summary(1, 40.0)})
    assert math.isnan(out.loc[2, "bag_floor_area"])


def test_non_residential_buildings_get_an_area_but_no_dwellings():
    out = apply_bag_floor_area_and_units(
        _buildings(), {"D": _summary(0, 280.0, n_non_res=1)})
    assert out.loc[3, "bag_floor_area"] == 280.0
    assert out.loc[3, "residential_units"] == 1.0
    assert out.loc[3, "residential_units_source"] == "rivm"


def test_repair_leaves_registered_dwelling_counts_alone():
    df = apply_bag_floor_area_and_units(_buildings(), {"B": _summary(2, 1500.0)})
    out = repair_dwelling_counts(df)
    # 1500 m2 over 2 dwellings would trigger a floor-area estimate, but the
    # count comes from the register and is kept.
    assert out.loc[1, "residential_units"] == 2.0
    assert out.loc[1, "residential_units_source"] == "bag"


def test_repair_estimates_from_the_registered_area_where_count_is_rivm():
    df = _buildings()
    df["bag_floor_area"] = [float("nan"), 1200.0, float("nan"), float("nan")]
    df.loc[1, "residential_units_recorded"] = 1.0
    df.loc[1, "residential_units"] = 1.0
    out = repair_dwelling_counts(df)
    # 1200 m2 for one dwelling is implausible; AB typical is 75 m2.
    assert out.loc[1, "residential_units"] == 16.0
    assert out.loc[1, "residential_units_source"] == "floor_area_estimate"
