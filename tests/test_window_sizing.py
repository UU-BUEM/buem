"""Window sizing from the archetype's own TABULA glazing share, and the
external shading factor on window solar gains.

Before 2026-11-25 every exposed LOD2 wall got DEFAULT_WINDOW_TO_WALL_RATIO
(0.5) as glazing. That figure was TABULA's mean window / *net* wall ratio
across all NL archetypes (incl. apartment blocks at 0.8-1.0), applied to
*gross* LOD2 walls: detached houses got 2-2.5x TABULA's glazing, which
inflated solar gains and cooling. Window gains also skipped F_sh_vert,
which the opaque terms already applied.
"""
from __future__ import annotations

import pandas as pd
import pytest

from buem.buildings.datasources.csv_source import CsvBuildingSource
from buem.buildings.mapping.archetype_spec import tabula_window_to_wall_ratio
from buem.config.building_registry import DEFAULT_WINDOW_TO_WALL_RATIO

LOENEN = "src/buem/data/buildings/netherlands/Loenen"


def test_ratio_is_window_over_gross_facade():
    row = pd.Series({"A_Window_1": 20.0, "A_Window_2": 5.0, "A_Wall_1": 60.0,
                     "A_Wall_2": 15.0, "A_Wall_3": 0.0})
    assert tabula_window_to_wall_ratio(row) == pytest.approx(25.0 / 100.0)


def test_ratio_none_without_areas():
    assert tabula_window_to_wall_ratio(pd.Series({"A_Wall_1": 50.0})) is None
    assert tabula_window_to_wall_ratio(pd.Series({"A_Window_1": 5.0})) is None


def test_nl_typology_ratios_by_type():
    """TABULA NL: detached clearly below the old 0.5 default, every
    residential archetype inside (0, 0.6)."""
    tabula = CsvBuildingSource(LOENEN).tabula
    nl = tabula[tabula["Code_BuildingVariant"].str.startswith("NL.N.")]
    ratios = {code: tabula_window_to_wall_ratio(row) for code, row in
              nl.set_index("Code_BuildingVariant").iterrows()}
    sfh = [r for c, r in ratios.items() if ".SFH." in c and r is not None]
    assert sfh and max(sfh) < 0.3 < DEFAULT_WINDOW_TO_WALL_RATIO
    assert all(0 < r < 0.6 for r in ratios.values() if r is not None)


def test_lod2_mapper_uses_archetype_ratio():
    from buem.analysis.provider_comparison import building_attrs_from
    from buem.buildings.mapping.lod2_mapper import LOD2Mapper

    mapper = LOD2Mapper(CsvBuildingSource(LOENEN), country="NL")
    building = mapper.map_building(1186)  # detached, many exposed walls
    elements = dict(building_attrs_from(building))["components"]
    windows = sum(e["area"] for e in elements["Windows"]["elements"])
    walls = sum(e["area"] for e in elements["Walls"]["elements"])
    share = windows / (windows + walls)
    assert share < 0.3  # was ~0.49 with the flat 0.5 default


def test_window_gains_scale_with_external_shading():
    import copy

    import numpy as np

    from buem.config.cfg_attribute import cfg as default_cfg
    from buem.thermal.model_buem import ModelBUEM

    def window_gain(f_sh: float) -> np.ndarray:
        cfg = copy.deepcopy(default_cfg)
        cfg["F_sh_vert"] = f_sh
        cfg["F_sh_hor"] = f_sh
        model = ModelBUEM(cfg)
        model._initPara()
        model._initEnvelop()
        model._init5R1C()
        return np.asarray(model.profiles["bQ_sol_Windows"])

    full, shaded = window_gain(1.0), window_gain(0.5)
    sky = full.min()  # the constant sky-radiation offset (no sun at night)
    np.testing.assert_allclose(shaded - sky, 0.5 * (full - sky), atol=1e-9)
