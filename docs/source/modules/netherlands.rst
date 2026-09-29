Netherlands Data Pipeline
============================

.. contents:: Sections
   :local:
   :depth: 2

Multiple regions, one pipeline
----------------------------------

Built for Loenen (Gelderland) first, but nothing in it is Loenen-
specific — every stage takes a region's CityJSON source and output
directory as plain arguments. Confirmed by actually running the full
pipeline against a second, independent community, Heeten (Overijssel,
municipality of Raalte), with **zero code changes** (2026-08-18):

.. list-table::
   :header-rows: 1
   :widths: 25 25 25 25

   * - Metric
     - Loenen
     - Heeten
     - Note

   * - Buildings (raw Pand extraction)
     - 3,105
     - 2,675
     - —

   * - **Real residential population** (issue #15, 2026-08-21)
     - **1,461**
     - **1,570**
     - 1,644/2,675 Pand records had no RIVM-registered dwelling unit
       (sheds/garages/outbuildings) and are excluded — see
       :doc:`../validation/loenen_cbs`'s "Correction" section

   * - RIVM id match rate
     - 99.4%
     - 100%
     - Stage 2 — against the raw 3,105/2,675 extraction, not the
       residential-only population

   * - Real energy label coverage
     - 23.9%
     - 23.9%
     - striking agreement — a stable national pattern, not a Loenen fluke

   * - TABULA archetype match rate
     - 100% (1,461/1,461 residential)
     - 100% (1,570/1,570 residential)
     - Stage 4 — of the real residential population, not the raw
       extraction

   * - Building type shape
     - SFH 64.5% / TH 32.8% / MFH 1.9% / AB 0.8%
     - SFH 66.0% / TH 30.5% / MFH 2.9% / AB 0.6%
     - Stage 3 — of the real residential population; the raw-extraction
       shape (SFH 80%+/TH 18%/apartment <1%) was itself a symptom of the
       contamination issue #15 fixed, not the real housing-type mix

Each region gets its own directory under
``src/buem/data/buildings/netherlands/`` (``Loenen/``, ``Heeten/``) —
kept separate rather than merged, since they're different municipalities
in different provinces and wouldn't map to one CBS regional benchmark
(see "Validation" below) either way. ``tabula.csv`` (country-level
reference data, not region-specific) is identical across regions —
just copied, not regenerated. The three small editable reference tables
(``u_value_reference.csv``, ``service_building_reference.csv``,
``refurbishment_measure_reference.csv``) are copied into both
``Loenen/`` and ``Heeten/`` (2026-08-20) — built and validated against
Loenen's CBS comparison, but genuinely country-level (Bouwbesluit/TABULA
construction-year data, not Loenen-specific content), so Heeten's batch
runs get the same corrections rather than silently falling back to
uncorrected TABULA values.


Why this pipeline exists
-------------------------

The Netherlands building dataset was originally a city2tabula-derived
CSV export, sharing the same shape :doc:`buildings`'s German
Excel/PostgreSQL pipeline consumes. That export turned out to carry two
independent, compounding duplicate-geometry bugs — a literal exact-
duplicate-row bug, and a subtler "the whole per-building extraction ran
twice" batch-duplication layer found only by cross-checking against
independent ground truth (see ``.claude/residential/resolved.md``'s
"Netherlands (Loenen) building data" entry for the full story). Rather
than keep patching an export whose *pipeline* had shown two separate
failure modes, the user (2026-08-16): "regenerate a clean Loenen database
with a proper single-pass LOD2/LOD3 extractor... Difficult to control a
pipeline within city2tabula which is not created by me."

The Netherlands pipeline below is the result: real geometry extracted
directly from CityJSON (3D BAG), and a real archetype/U-value link built
independently of city2tabula's own (per the user, 2026-08-17: "I do not
fully trust the TABULA and 3D BAG/LOD2 building mapping done by
city2tabula") linking logic — while still trusting, and reusing, TABULA's
own *published* Dutch archetype data, which is independently cross-
validated below before being trusted.

Four stages, each its own module, each independently re-runnable:

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - Stage
     - Module
     - Produces

   * - 1. Geometry
     - ``cityjson_extractor``
     - ``lod2_building_feature.csv`` / ``lod2_child_feature_surface.csv``
       (real wall/roof/floor geometry, party walls, storeys, centroid)

   * - 2. Energy labels
     - ``rivm_energy_labels``
     - Per-building real Dutch energy label + residential-unit count,
       queried from the RIVM GeoPackage

   * - 3. Building type
     - ``nl_building_classifier``
     - ``building_type``/``neighbour_status`` (SFH/TH/MFH/AB,
       B_Alone/B_N1/B_N2)

   * - 4. Archetype + U-values
     - ``nl_archetype_mapper``
     - ``tabula_variant_code_id`` linking each building to a real TABULA
       row, plus the editable U-value override table


Stage 1 — Geometry from CityJSON
----------------------------------

``buem.buildings.datasources.cityjson_extractor``, run via
``python -m buem.buildings.datasources.cityjson_extractor <path.city.json>
--output <dir>`` (deterministic: buildings are assigned ``building_
feature_id`` in sorted-BAG-pand-id order, so re-running against the same
source reproduces the same ids). Column-compatible with
``lod2_building_feature.csv``/``lod2_child_feature_surface.csv``
everywhere else in :doc:`buildings` already expects, so ``CsvBuildingSource``/
``LOD2Mapper`` need no changes to consume its output.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Step
     - Method

   * - Face area
     - Newell's method, summed across *every* ring of a face (exterior
       boundary plus any interior rings/holes) before taking the vector's
       magnitude — courtyards/light-wells subtract correctly because
       CityJSON winds interior rings opposite the exterior ring, so their
       contribution cancels rather than adding. Confirmed on 144 real
       holed faces (128 of 3,105 Loenen buildings) before trusting this —
       ignoring holes had over-counted area by 3,906 m² in aggregate, up
       to ~37% for one individual face.

   * - Wall tilt / azimuth
     - Tilt fixed at 90°. Azimuth = ``atan2(nx, ny) mod 360`` from the
       face's own outward unit normal (``nx``, ``ny`` = its horizontal
       component) — computed from real geometry, not read from an
       attribute.

   * - Roof tilt / azimuth
     - Both computed from the face's outward normal
       (``tilt = degrees(acos(nz))``, ``azimuth`` as above) — unlike the
       German path's 0°-azimuth placeholder (see :doc:`buildings`'s
       "Assumptions"), a real per-plane value is directly available here.
       Formula and the outward-normal orientation convention were
       validated against 3D BAG's own published ``b3_azimut``/
       ``b3_hellingshoek`` roof attributes on real buildings before being
       trusted (matched to within rounding, e.g. 307.89328° vs. computed
       307.9°).

   * - Floor tilt / azimuth
     - Always 0°/0°, matching the German-path convention (not derived
       from the geometric downward-facing normal).

   * - Party walls
     - CityJSON gives each building its own self-contained solid — no
       shared id to key off, unlike the German path's "``surface_
       feature_id`` appears under two buildings" convention. A real
       alternative signal was checked and rejected first: 3D BAG's own
       ``on_footprint_edge`` wall attribute does **not** correlate with
       the shared boundary between two confirmed-touching buildings
       (checked directly against a real adjacent pair before writing any
       code around it). Detected instead by 2D geometric coincidence: two
       wall faces from different buildings are the same party wall when
       their vertical-wall-projected line segments are nearly collinear
       (perpendicular distance < 0.3 m) and substantially overlapping
       (≥ 50% of the shorter segment's length) — thresholds calibrated
       against a real known-touching pair (true matches measured 93–100%
       overlap/<0.09 m; false candidates 0–7%/wider gaps). A matched pair
       is written back out with the *same* ``surface_feature_id`` under
       both buildings, so ``SharedWallDetector``/``LOD2Mapper`` consume
       it unchanged.

   * - Storeys
     - 3D BAG's own ``b3_bouwlagen`` estimate when present (~42% of
       buildings; its documented scope is "up to 5 estimated floors" —
       confirmed not a practical limitation for Loenen, where <1% of
       real buildings exceed 5 floors). Otherwise
       ``round((b3_h_dak_50p - b3_h_maaiveld) / 2.8 m)``, floored at 1.

   * - Non-residential exclusion
     - 3D BAG's ``b3_kas_warenhuis`` (greenhouse/warehouse) and
       ``b3_is_glas_dak`` (glasshouse roof) flags — 2 + 2 = 4 of 3,105
       Loenen buildings — mark ``is_greenhouse_or_warehouse``/
       ``is_glass_roof``, read by Stage 3 to exclude these from
       residential archetype matching entirely.

**Known limitation**: a wall face that genuinely touches two *different*
neighbouring segments (a footprint step along a shared boundary) only
gets its single best-overlap match recorded, not a geometric split
(confirmed to occur at least once during calibration). A deliberate
effort/correctness tradeoff, not an oversight.

**Checked and closed, not assumed**: whether a shared wall between two
``BuildingPart``s of the *same* ``Building`` needed separate "internal
partition" handling. Only one such multi-part building exists in all of
Loenen; running the same coincidence math between its own two parts found
zero genuine matches — consistent with 3D BAG's LOD2.2 "shell" modeling
(each part a complete, independent exterior envelope) not producing
internal walls at all.


Real (latitude, longitude) — the RD New → WGS84 transform
------------------------------------------------------------

Every building's centroid is written by ``cityjson_extractor`` as a
hex-encoded EWKB POINT in its *raw* CRS — RD New / Amersfoort, EPSG:28992
(the Dutch national triangulation grid; CityJSON's own ``transform``
block confirms this for a real Loenen source file), not pre-converted —
so it round-trips through the exact byte layout
``buem.buildings.mapping.geometry_utils.wkb_point_to_lat_lon`` already
decoded for the (now-retired) city2tabula export, unchanged.

That decoder does the actual transform, via ``pyproj``:
``pyproj.Transformer.from_crs("EPSG:28992", "EPSG:4326", always_xy=True)``
(one transformer instance per SRID, built lazily and cached — transformer
*construction* is the expensive part, not the per-point transform itself).
``building_lat_lon(bldg_row)`` wraps this for a whole building row,
returning ``None`` (not raising) when the geometry is absent or
malformed, logged as a warning.

**Wired into ``LOD2Mapper`` as of 2026-08-17** (previously a real gap,
found via a *German* building that got weather fetched at the wrong
country's coordinates because ``BuildingIdentity.latitude``/``longitude``
silently kept their (52.0, 5.0) class defaults): ``map_building()``'s
identity-construction step now calls ``geometry_utils.building_lat_lon()``
on the source row and uses the result when available, leaving the class
default only when no real geometry exists at all (e.g. most current
German Excel rows, which have no ``building_centroid_geom`` — a
pre-existing, separate, still-open gap, see
``.claude/residential/open.md``). This is why a real weather fetch for a
Netherlands building now lands in Gelderland rather than the module
default, and matters concretely for occupancy's ``to_buem_profiles()``
scaling too (both consume real lat/lon downstream once a Building object
exists).


Stage 2 — RIVM energy labels
-------------------------------

``buem.buildings.datasources.rivm_energy_labels`` queries
``energielabels_2025.gpkg`` — RIVM's real, nationwide (~11.35M row,
~3.2 GB) export of registered Dutch building energy labels. **Never
loaded in full**: a GeoPackage is plain SQLite under the hood, so this
module queries it with the stdlib ``sqlite3`` module directly — a
targeted ``WHERE identificatie IN (...)`` against a specific list of BAG
Pand ids, chunked to respect SQLite's host-parameter limit, never a
pandas/geopandas full-table read.

Join key: ``identificatie`` is the *raw* 16-digit BAG Pand id (e.g.
``"0200100000938969"``) — **no** ``NL.IMBAG.Pand.`` prefix, unlike
``cityjson_extractor``'s own ``bag_pand_id`` column, which does carry it.
``strip_bag_prefix()`` handles the conversion; the join itself is exact,
not spatial.

Real coverage, confirmed for Loenen (2026-08-17): **3,087/3,105 buildings
(99.4%) match by id** at all, but only **742 (23.9%) have a real,
non-null** ``dominant_label`` — most Dutch buildings have simply never
had an energy label registered (close to the ~30% national average).
This is why the label is used as an *override* where present, not the
sole archetype-matching signal (see Stage 4).

Dwelling counts, and repairing the ones that cannot be right
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``aant_verblijfsobj`` (residential-unit count) is used twice, in opposite
directions: it scales one household's occupancy-generated internal gains
up to a whole block *before* the solve, and divides the whole-building
result back down for comparison against per-dwelling statistics *after*
it. A wrong count therefore corrupts both.

A single BAG *Pand* can legitimately be an entire terrace or apartment
block housing many households — normal Dutch building stock, and what
TABULA's own AB/MFH archetypes model (their ``n_Apartment`` runs 15–56).
But RIVM sometimes registers only part of a Pand's sub-units, leaving
buildings that imply impossible dwelling sizes: **169 of 3,105 Loenen
buildings (5.4%)** implied more than 500 m² per dwelling, the worst at
42,204 m², including one 19,241 m² block recorded as holding two.

``nl_archetype_mapper.repair_dwelling_counts()`` derives a count from
floor area where the registered one cannot be right, via
``scripts/repair_nl_dwelling_counts.py``. Three columns are written so a
derived value can never be mistaken for registered data:

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Column
     - Meaning

   * - ``residential_units_recorded``
     - Exactly what RIVM registered, preserved

   * - ``residential_units_source``
     - ``rivm`` or ``floor_area_estimate``

   * - ``residential_units``
     - The value everything downstream uses

The estimate divides real floor area (``area_total_floor`` × storeys) by
a typical dwelling size for the building type — 150 m² SFH, 120 TH, 90
MFH, 75 AB. Two guards keep it conservative: it **never reduces** a
registered count (the sub-units RIVM does list genuinely exist), and it
does not act at all where the implied dwelling size is already plausible.
Re-running is a no-op.

Result for Loenen: 169 repaired, 0 implausible remaining — 152 SFH, 10
TH, 4 MFH, 1 AB and 2 non-residential.

**Known limitation**: for the 152 SFH cases the repair assumes the floor
area really is dwellings. Some are more likely large agricultural
buildings, where neither one dwelling of 2,000 m² nor thirteen of 150 m²
is right. The estimate at least yields a plausible per-dwelling
intensity; distinguishing barns from housing needs a use-class signal the
pipeline does not currently carry.


Stage 3 — Building-type classification
------------------------------------------

``buem.buildings.mapping.nl_building_classifier`` replaces city2tabula's
(mistrusted) linking between a building and TABULA's
``Code_BuildingSizeClass``/``Code_AttachedNeighbours`` — not with a new
invented scheme, but by reproducing **CBS's own published methodology**
for deriving ``woningtype`` (Statistics Netherlands, the national
statistics office):

    "de[rivation] ... is based on a modeling approach where the number of
    connected BAG buildings and the number of BAG residential objects
    with their use function determines the assignment of housing type"

Both signals CBS names are already available here without needing CBS's
own access-gated microdata product (see "CBS microdata access" below):
the *number of connected BAG buildings* is Stage 1's own geometric
party-wall detection (``attached_neighbour_id``); the *number of
residential objects* is Stage 2's ``aant_verblijfsobj``.

.. list-table::
   :header-rows: 1
   :widths: 25 30 25

   * - CBS category (Dutch)
     - CBS rule
     - buem mapping

   * - vrijstaand
     - 0 connections
     - SFH, B_Alone

   * - twee-onder-een-kap
     - 1 connection, a pair (component size 2)
     - SFH, B_N1

   * - hoekwoning (corner house)
     - 1 connection, in a row of 3+
     - TH, B_N1

   * - tussenwoning (mid-terrace)
     - 2+ connections
     - TH, B_N2

   * - meergezinswoning
     - 2+ residential units in one Pand
     - MFH (≤4 units) or AB (>4)

The right-hand "buem mapping" column is buem's own judgment call, not
from CBS — CBS's 5 categories don't correspond 1:1 to TABULA's 4. Two
choices worth being explicit about:

- **Corner house → TH, not SFH.** A corner/end unit of a row only
  touches one neighbour (structurally like a semi-detached pair), but is
  architecturally a row house, not a detached-family form — TABULA's own
  SFH/TH split follows building *typology*, and a corner unit's
  construction (party wall on one full side, narrow street frontage) is
  a row-house typology even at the end of the row.
- **MFH vs. AB has no CBS- or TABULA-published threshold** — the
  ``MFH_MAX_UNITS`` split (≤4 units) is buem's own first-pass heuristic,
  same framing as ``cfg_attribute.DEFAULT_ARCHETYPE_BY_BUILDING_TYPE``'s
  own "first-pass heuristic, not a derivation" disclaimer: revisit with
  real data if/when available.

Non-residential buildings, linked to occupancy's service-building path
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A building flagged non-residential (Stage 1's ``is_greenhouse_or_
warehouse``/``is_glass_roof``) always gets ``is_residential=False`` and
no TABULA ``building_type`` — excluded from residential archetype
matching either way. But **not** fully excluded from modeling (2026-08-18,
per the user): a large enough one (``footprint_area >=
MIN_SERVICE_BUILDING_FOOTPRINT_M2``, 50 m²) additionally gets a real
``service_building_type``, linking it to one of occupancy's 8 registered
``services_buildings.SERVICE_BUILDING_TYPES`` (currently always
``"warehouse"`` — the closest match, since ``b3_kas_warenhuis`` conflates
greenhouse *and* department store under one flag, and neither of
occupancy's other 7 types fits better).

The 50 m² threshold isn't arbitrary — checking the flagged buildings'
own real footprint areas before routing them uniformly surfaced a clean
split, not assumed: Loenen's two ``b3_kas_warenhuis`` buildings are
genuinely large (2,125 m² / 1,718 m², real commercial-scale structures),
while its two ``b3_is_glas_dak`` buildings are tiny (16 m² / 5 m²) — too
small to be a real occupied structure, almost certainly a garden
greenhouse/conservatory. Those stay excluded from *both* residential and
service-building modeling rather than being force-fit into either.
Heeten reproduces the same shape (4 flagged: 3 large enough for
``"warehouse"``, 1 too small).

Simulating a service building
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Carrying a ``service_building_type`` was originally only half the link.
``AttributeBuilder`` already routed non-residential buildings to
occupancy's ``ServiceBuildingProfile``, but they never got that far:
``LOD2Mapper`` resolves a building's thermal description from its matched
TABULA row, TABULA is a *residential* typology, and so every service
building returned ``None`` and was skipped.

Closed by giving the mapper a second archetype source. Both paths now
produce a
:class:`~buem.buildings.mapping.archetype_spec.ArchetypeSpec`, so the
geometry, opening-synthesis and element-assembly steps are identical
regardless of where the thermal description came from:

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - Building
     - Archetype source
     - Internal gains

   * - Residential
     - Matched TABULA variant row
     - ``occupancy.HouseholdProfile``

   * - Service
     - ``service_building_reference.csv``
     - ``occupancy.ServiceBuildingProfile``

``src/buem/data/buildings/netherlands/Loenen/service_building_reference.csv``
carries one row per (``service_building_type``,
``construction_year_class``) — all 8 of occupancy's registered types,
across the same 6 Dutch construction eras. Provenance differs by column,
and is worth being explicit about:

- **Envelope U-values** follow the same Bouwbesluit construction-year
  series already cross-checked for the residential path (see "The
  editable U-value table" below). Bouwbesluit's thermal requirements are
  not residential-specific, so the same series applies.
- **Use parameters** — room height, ventilation rate, infiltration,
  thermal mass, heating-reduction factor — differ by building category
  and are **first-pass engineering values**, not a published table. A
  warehouse gets 6 m rooms, low ventilation and intermittent heating
  (``F_red_htr`` 0.8); a school gets high ventilation for its occupancy
  density; a restaurant higher still. Same framing as
  ``MFH_MAX_UNITS``: revisit with real data if it becomes available.

Two deliberate choices in the fallback: a service building is always
``B_Alone`` (no party-wall typology applies, so its full envelope is
exposed), and ``phi_int`` is left unset, because internal gains come from
occupancy's own per-category model rather than a static archetype figure
— setting both would double-count.

A building with no ``construction_year_class`` falls back to the oldest
era for its type: an unknown-age commercial structure is far more likely
old than new, and modelling it as new would understate its demand.

Real result for Loenen: both ``warehouse`` buildings now simulate
(2,125 m² and 1,718 m², ≈273 kWh/m² — comparable to the residential
NL.01 mean of 266). The two sub-threshold glass-roof structures stay
skipped, which is correct: they carry no ``service_building_type`` at all.

CBS microdata access
^^^^^^^^^^^^^^^^^^^^^

CBS's own ``woningtype`` *dataset* itself (not just its published
methodology, which this module reproduces independently) is available
via CBS microdata — but this is a real institutional process, not a
personal sign-up, even with a university affiliation: (1) the researcher's
*institution* must first be authorized by CBS as eligible (Dutch
universities/research institutions, and a few other EU countries'); (2) a
per-project application through the CBS microdata portal, reviewed for
feasibility and GDPR compliance, with a cost estimate (billed quarterly,
scales with researchers/datasets/duration); (3) work happens inside a
CBS-managed remote-access environment, after training and an awareness
test; (4) results must be publicly published, citing CBS/Kadaster. Worth
pursuing if a university's own CBS liaison confirms existing institutional
access, but not something this pipeline depends on — it already
reproduces the documented method directly from open Dutch data.


Stage 4 — TABULA archetype linking + U-values
--------------------------------------------------

``buem.buildings.datasources.nl_archetype_mapper`` resolves each
residential building's real TABULA archetype row, from real signals
independent of city2tabula:

1. **The real construction year determines the era**, bucketed into
   TABULA NL's own class boundaries (read directly from the bundled
   ``tabula.csv``): NL.01 ≤1964, NL.02 1965–1974, NL.03 1975–1991,
   NL.04 1992–2005, NL.05 2006–2014, NL.06 2015+ — boundaries that
   align with the real Dutch Bouwbesluit code-change history
   (1965/1975/1992/2015). The era is never overridden: a renovated 1980
   building is still structurally a 1980 building, with that era's
   geometry, storey count and thermal mass.
2. **A real energy label, when present (24%), selects the refurbishment
   variant within that era.** TABULA provides three variant rows per
   archetype (``Number_BuildingVariant`` 1/2/3: as-built, standard
   refurbishment, nZEB refurbishment). NTA 8800/ISSO 82.1 associate each
   construction era with a typical baseline label
   (``LABEL_TO_YEAR_CLASS``); a label materially better than its era's
   typical level indicates the building has been refurbished. The gap in
   year-class steps drives the choice — 1–2 tiers better selects the
   standard-refurbishment variant, 3 or more the nZEB variant
   (``label_to_refurbishment_variant()``). Buildings without a label keep
   the as-built variant, so an undetected refurbishment cannot currently
   be reflected.

Both resolve via ``tabula_helpers.lookup_tabula_archetype()`` — the same
selection logic (prefer a ``.Gen.`` variant, then the requested variant
number, then the lowest id for determinism) :doc:`buildings`'s German
path and the live-request path already use. A match sets
``tabula_variant_code_id``/``tabula_variant_code`` to that real row's own
``id``/``Code_BuildingVariant``, so ``LOD2Mapper.map_building()`` picks
up the correct variant with no further linking step.

Refurbishment measures
^^^^^^^^^^^^^^^^^^^^^^^^

The three variant rows of an archetype share identical base
``U_<Component>_1`` columns; the refurbished performance lives in
per-component measure columns (``Code_MeasureType_<Component>_1``,
``R_PredefinedMeasure_<Component>_1``).
``tabula_helpers.apply_refurbishment_measures()`` converts those into
adjusted U-values, applied in ``LOD2Mapper.map_building()`` after the
editable override table so measures compound with whichever base U-value
is in effect:

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Measure type
     - Meaning
     - Adjustment

   * - ``Add``
     - Insulation added to the existing construction
     - ``U_new = 1 / (1/U_old + R)`` (resistances in series)

   * - ``Replace``, ``ReplaceInsulation``
     - Component or its insulation layer replaced outright (e.g. new glazing)
     - ``U_new = 1 / R``

   * - ``0`` / absent
     - No measure (all as-built variant rows)
     - unchanged

Correcting a measure whose published performance has aged
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

TABULA states each measure's performance as it stood when the typology
was compiled, which can lag the market. NL's standard window measure
``NL.Window.Ins.01`` assumes R = 0.556, i.e. **U = 1.80** — plain HR
glazing — while Dutch housing refurbished to that same energy-label tier
today typically has **HR++ at 1.1–1.2**. Checked against a real label-B
1965–74 MFH, the opaque components landed sensibly (wall 0.254 against a
0.35–0.40 label-B typical, roof and floor in range) and the window was
the sole outlier, ~55% worse than reality.

``refurbishment_measure_reference.csv`` corrects the measure itself
rather than patching each affected archetype, so the correction applies
everywhere that measure is used:

.. list-table::
   :header-rows: 1
   :widths: 25 12 12 51

   * - ``measure_code``
     - ``R_value``
     - implied U
     - Rationale

   * - ``NL.Window.Ins.01``
     - 0.870
     - 1.15
     - HR++ double glazing, low-e coated and argon filled — what a
       building refurbished to this tier actually has installed today

This matters more since glazing was raised to 50% of wall area: excess
window conductance scales with both the U-value error and the glazed
area, so the two compound. Absent, the file is simply not applied and
TABULA's published value stands.

Real result for Loenen (2026-08-21, real residential population per
issue #15 — see "Multiple regions" table above): **1,461/1,461
residential buildings (100%) matched** — 742 via a real label, 719 via
construction year. Type distribution: SFH 942 (64.5%), TH 479 (32.8%),
MFH 28 (1.9%), AB 12 (0.8%). Of the 742 label-matched buildings, 322
resolve to the standard-refurbishment variant and 86 to the nZEB variant;
the remaining 334 carry a label matching their era's typical level and
stay as-built.

The editable U-value table
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

TABULA's own Dutch archetype *data* is trusted here even though its
city2tabula *linking* isn't (see "Why this pipeline exists" above) — and
that trust isn't asserted blind. TABULA NL's real ``U_Wall_1`` values,
converted to Rc = 1/U, were cross-checked against the independently-
researched Dutch Bouwbesluit/NTA 8800 historical Rc-value requirements
and matched almost exactly at every checkable point:

.. list-table::
   :header-rows: 1
   :widths: 20 25 25 15

   * - Year class
     - TABULA NL U_Wall
     - Bouwbesluit 1/Rc
     - Match

   * - NL.02 (1965–1974)
     - 2.326
     - 2.326 (Rc 0.43)
     - exact

   * - NL.03 (1975–1991)
     - 0.769
     - 0.769 (Rc 1.30)
     - exact

   * - NL.04 (1992–2005)
     - 0.395
     - 0.400 (Rc 2.50)
     - within 1%

   * - NL.06 (2015+)
     - 0.222
     - 0.222 (Rc 4.50)
     - exact

(NL.05's own comparison point differs more (TABULA 0.286 vs. the
Bouwbesluit table's single combined "1992–2014" minimum of 0.400) — TABULA
splits that period further into NL.04/NL.05, capturing that later
Bouwbesluit-era Dutch construction often exceeded the regulatory
*minimum* in practice, a real, expected nuance rather than a
discrepancy.)

Per the user (2026-08-17: "we make a clean table with year of
construction in an axis and building type in another, providing U values
that users can easily change if needed"), these values are additionally
published as a small, plain, human-editable file —
``src/buem/data/buildings/netherlands/Loenen/u_value_reference.csv`` — rather
than requiring anyone to hand-edit a 200-column TABULA row. Columns:
``construction_year_class``, ``building_type``, ``U_Wall``, ``U_Roof``,
``U_Floor``, ``U_Window``, ``U_Door``.

**Editing this file actually changes simulation results** — it isn't
static documentation. ``LOD2Mapper.__init__``'s optional
``u_value_overrides`` parameter (a loaded copy of this CSV) is checked,
per building, against the resolved TABULA row's own
``Code_ConstructionYearClass``/``Code_BuildingSizeClass``; a match
replaces that row's own wall/roof/floor/window/door U-values before they
reach ``ThermalProperties`` — every *other* TABULA parameter
(``n_air_infiltration``, ``c_m``, ``h_room``, ``F_red_htr``, ``theta_i``,
window ``g_gl``, etc.) still comes from the real matched TABULA row,
unaffected. ``u_value_overrides=None`` (the default) preserves the
original TABULA-row-only behavior exactly — no effect on the German path.


Re-running the full pipeline
--------------------------------

.. code-block:: python

   import pandas as pd
   from buem.buildings.datasources.rivm_energy_labels import load_labels_for_buildings
   from buem.buildings.datasources.nl_archetype_mapper import map_buildings

   # Stage 1 (CLI): python -m buem.buildings.datasources.cityjson_extractor <path.city.json> --output <dir>

   buildings_df = pd.read_csv("<dir>/lod2_building_feature.csv")
   nl_tabula = pd.read_csv("<dir>/tabula.csv", na_values=["NULL"])
   nl_tabula = nl_tabula[nl_tabula["Code_Country"] == "NL"]

   # Stage 2
   rivm = load_labels_for_buildings("<path to energielabels_*.gpkg>", buildings_df["bag_pand_id"].tolist())

   # Stages 3 + 4
   result = map_buildings(buildings_df, nl_tabula, rivm)
   result.to_csv("<dir>/lod2_building_feature.csv", index=False)

   # then, wherever the building is mapped:
   overrides = pd.read_csv("<dir>/u_value_reference.csv")
   mapper = LOD2Mapper(CsvBuildingSource("<dir>"), country="NL", u_value_overrides=overrides)


Running the model over a region
----------------------------------

Setup
^^^^^^^

.. code-block:: bash

   conda activate buem_env
   cd <repo root>          # --data-dir is resolved relative to the cwd
   buem validate

``WEATHER_DATA_DIR`` must resolve to real processed provider archives (the
NetCDF output of ``weather run --provider ...``) before anything imports
``buem``; the repo's ``.env`` sets it on a configured machine. Note that
``buem validate`` checks ``BUEM_WEATHER_DIR``/``BUEM_RESULTS_DIR``/
``BUEM_LOG_DIR`` only — it can report PASS while a run still fails at
import for want of an archive.

Defaults, and why they are what they are:

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Setting
     - Default
     - Source / rationale

   * - Weather provider
     - ``merra-2``
     - ``building_registry.DEFAULT_WEATHER_PROVIDER``. ``era5-land``
       currently fails at this cell/year (an unrepaired de-accumulation
       boundary); ``cosmo-rea6`` works if selected explicitly.

   * - Weather year
     - 2018
     - ``building_registry.DEFAULT_YEAR`` — the year archive access is
       verified working for.

   * - CBS period
     - ``2018JJ00``
     - Derived as ``<weather-year>JJ00`` so the two cannot drift apart.
       Real Apeldoorn gas consumption roughly halved between 2018 and
       2024, so a year mismatch is a large error, not a rounding one.

   * - Region / weather point
     - derived
     - The mean real centroid of the region's own buildings
       (``geometry_utils.region_center_lat_lon``). One fetch is shared
       across the whole run: a village spans a few kilometres, well
       inside one reanalysis grid cell.

   * - Comfort dead-band
     - 18–21 °C
     - ``building_registry.DEFAULT_COMFORT_T_LB``/``_UB``.

   * - Window-to-wall ratio
     - 0.5
     - ``building_registry.DEFAULT_WINDOW_TO_WALL_RATIO``, applied to each
       exposed wall; overridable per request.

Whole-region run
^^^^^^^^^^^^^^^^^^

``buem.analysis.batch`` runs every building through the same
``LOD2Mapper`` → ``AttributeBuilder`` → ``CfgBuilding`` → ``ModelBUEM``
path as a single-building run, across a ``ProcessPoolExecutor``, writing
one row per building to Parquet incrementally. ``--source csv`` selects a
``CsvBuildingSource`` region; ``--source excel`` (the default) is the
German workbook path.

.. code-block:: bash

   python -m buem.analysis.batch --source csv \
       --data-dir src/buem/data/buildings/netherlands/Loenen \
       --country NL --residential-only \
       --workers 16 --resume \
       --output results/loenen.parquet

Worth knowing:

- **One weather fetch** is made in the parent and handed to each worker
  once via the pool initializer, rather than pickled per building.
  ``LOD2Mapper`` is likewise built once per worker, not once per task.
- **``--resume`` is safe to pass always.** Building ids already in the
  output are skipped and their rows carried through, so an interrupted
  run continues instead of restarting.
- **``u_value_reference.csv`` is picked up automatically** from
  ``--data-dir``, so a batch run and a validation run of the same region
  apply identical U-values.
- **``--residential-only`` / ``--labeled-only``** filter on the
  ``is_residential`` / ``matched_via_label`` columns Stage 3/4 produce.
  Asking for a filter a source cannot honour raises rather than quietly
  running the unfiltered population.
- One building failing is recorded as an ``error`` row, never an aborted
  run.

On Linux, ``scripts/run_region_batch.sh`` wraps this: it checks
``WEATHER_DATA_DIR``, pins the BLAS thread count to 1 per worker (the
per-building linear algebra is small, and nested thread pools
oversubscribe the cores), sizes ``--workers`` to the box, and detaches
under ``nohup`` so an SSH session can drop without killing the run.

Measured throughput, 16 workers on a 22-logical-core machine: **~1.95
buildings/s**, i.e. about 27 minutes for all 3,101 residential Loenen
buildings. Memory is a few hundred MB per worker. The per-building cost
is dominated by the LP solve (4 × 8760 = 35,040 variables, CLARABEL with
an OSQP fallback), so it varies little between buildings — meaning
throughput scales with worker count, and a whole community is a
laptop-scale job, not one that needs a cluster.

``tests/test_pipeline_scaling.py`` wraps this same run (and the smaller
``ParallelBuildingProcessor`` path) as a graduated pytest scale ladder --
from a single building up through Heeten/Loenen to an externally-supplied
larger region -- for benchmarking where time is spent across the
weather/occupancy/buem chain rather than validating buem's own output.


Validation
--------------

- **PostgreSQL/3DCityDB access confirmed working (2026-08-18)** — the
  bundled ``.env`` credentials connect to a live database that is
  actually a full 3DCityDB v5 instance (the tool
  <https://github.com/3dcitydb>), with ``city2tabula``/``lod2``/``lod3``/
  ``tabula`` schemas alongside it. Used once already: ``city2tabula
  .lod2_building_feature``/``lod2_child_feature_surface`` row counts
  (3,782 / 286,579) match the old buggy CSV export *exactly*, confirming
  the Netherlands duplicate-row bug lives in the database itself, not
  merely an export artifact — a second, independent confirmation of what
  "Why this pipeline exists" already concluded. Its ``lod3_*`` tables
  exist structurally but are completely empty (0 rows) — confirms no
  real LOD3 (window/door) geometry is accessible anywhere for this
  region; the ratio-based synthesis in :doc:`buildings` remains the only
  available approach, not a stand-in for queryable real data.
- **A real, ready-to-use regional benchmark exists, not yet wired into a
  comparison script**: CBS opendata table ``81528NED`` ("Energieverbruik
  particuliere woningen; woningtype en regio's") publishes real average
  gas (m³)/electricity (kWh) consumption per dwelling, freely queryable
  via OData (no CBS-microdata gating), filterable by municipality *and*
  by exactly the housing-type categories this pipeline's own Stage 3
  already reproduces. Real 2024 figures for Apeldoorn (``GM0200`` —
  Loenen's own municipality):

  .. list-table::
     :header-rows: 1
     :widths: 40 30 30

     * - Housing type
       - Gas (m³/yr)
       - Electricity (kWh/yr)

     * - All dwellings
       - 860
       - 2,620

     * - Detached (SFH, B_Alone)
       - 1,380
       - 4,010

     * - Semi-detached (SFH, B_N1 pair)
       - 1,020
       - 3,110

     * - Corner (TH, B_N1)
       - 890
       - 2,630

     * - Mid-terrace (TH, B_N2)
       - 770
       - 2,460

     * - Apartment (MFH/AB)
       - 590
       - 1,800

  Query: ``https://opendata.cbs.nl/ODataApi/odata/81528NED/TypedDataSet
  ?$filter=RegioS eq 'GM0200'``.

**Built 2026-08-18** — ``buem.analysis.netherlands`` (a new subpackage,
alongside the NL-specific *data* modules under ``buildings.datasources``/
``mapping`` but matching ``buem.analysis``'s own "run simulations, compare
results" charter rather than a data-ingestion one):

- ``cbs_reference`` — the real CBS 81528NED OData client (stdlib
  ``urllib``, no new dependency), plus the buem↔CBS housing-type mapping.
- ``gas_conversion`` — the m³ → useful-heat-kWh chain, as three
  separate, independently-documented constants (not one collapsed
  ratio, per the user, 2026-08-18: "Why value of gas → heat conversion
  are you considering, let me know?"):

  1. **Calorific value, 9.769 kWh/m³** (35.17 MJ/m³) — the official
     Dutch/Gasunie billing standard, verified against multiple
     independent sources. Solid, not a judgment call.
  2. **Space-heating share of total gas, 78%** — CBS's own gas figure
     covers heating *and* hot water (~20%) *and* cooking (~2%); checked
     directly that ``ModelBUEM`` simulates space heating only (``q_w_nd``,
     TABULA's hot-water parameter, is carried in config but never read by
     ``sim_model()``), so this share has to come out on the CBS side.
     Source: CBS's own 2016 national breakdown — a blended average, the
     part most worth refining with per-type data if this is revisited.
  3. **Boiler efficiency, 90%** — gas energy in ≠ useful heat delivered;
     a modern condensing boiler recovers ~90-96% of the upper calorific
     value. A round starting assumption, not a stock-weighted average
     across the real mix of boiler ages — the single factor here most
     worth revisiting.

- ``validation`` — the runner: groups real buildings by
  (``building_type``, ``neighbour_status``), runs each through the same
  real ``AttributeBuilder``/``CfgBuilding``/``ModelBUEM`` path
  ``buem.analysis.batch`` uses (one shared regional weather fetch, real
  U-value overrides), and reports simulated heating demand alongside the
  CBS-derived figure. Two entry points:

  .. code-block:: bash

     # Sampled smoke test: first N buildings per group, minutes to run
     python -m buem.analysis.netherlands.validation \
         --data-dir src/buem/data/buildings/netherlands/Loenen \
         --region-code GM0200 --samples-per-type 5 [--labeled-only]

     # Population-complete: aggregate a finished batch run, no simulation
     python -m buem.analysis.netherlands.validation \
         --from-parquet results/loenen.parquet --region-code GM0200
     python -m buem.analysis.netherlands.validation \
         --from-parquet results/loenen.parquet --region-code GM0200 --labeled-only

  ``--from-parquet`` exists because the sampled path takes the **first N
  buildings in file order**, and that order is not random with respect to
  construction era — see "sampling skew" below, where the effect is
  measured rather than assumed. Aggregating a whole-region batch run has
  no sample to skew, and every slice (all buildings, label-matched only)
  comes from the same simulation rather than a separate one, so the two
  are directly comparable. Both paths share the CBS lookup, conversion
  and reporting code, so they cannot drift apart. Each building's
  whole-building result is divided by its own ``residential_units``
  before the group mean, matching CBS's per-dwelling figures.

  Both paths need internet: the CBS 81528NED figure is fetched live.

**First real run, Loenen, 2 buildings/group** — immediately useful, not
just a clean pass: SFH results landed in a plausible range (ratio 0.72-
4.80), but AB/MFH came back 10-80× too high. Checked why rather than
shipped as-is: those `building_feature_id` rows are whole apartment
*buildings* with real 257-756 m² footprints (multiple real dwelling
units each), while CBS's figure is *per dwelling* — comparing a whole
building's simulated total against a per-dwelling reference was the
actual bug, not a buem modeling error. Fixed by carrying RIVM's real
``aant_verblijfsobj`` (dwelling-unit count) through as a new
``residential_units`` column (``nl_archetype_mapper``) and dividing the
simulated total by it before comparing — a no-op for SFH/TH (already one
dwelling per building) but essential for MFH/AB.

**Second real run, matched years (2018 CBS vs. 2018 weather, both now the
default), 5 buildings/group** — ruled out small-sample noise as TH's
explanation: TH's B_N1 (6.38×) and B_N2 (6.39×) ratios agree to within
0.2% across 5 *different* buildings each, the signature of a systematic
effect, not random variance. Full results:

.. list-table::
   :header-rows: 1
   :widths: 15 15 10 15 15 15 15

   * - type
     - neighbour
     - n
     - buem kWh
     - CBS m³/yr
     - CBS→kWh
     - ratio
   * - AB
     - B_Alone
     - 5
     - 42,226
     - 890
     - 6,103
     - 6.92
   * - AB
     - B_N1
     - 4
     - 12,674
     - 890
     - 6,103
     - 2.08
   * - MFH
     - B_Alone
     - 5
     - 35,425
     - 890
     - 6,103
     - 5.80
   * - MFH
     - B_N1
     - 5
     - 40,583
     - 890
     - 6,103
     - 6.65
   * - SFH
     - B_Alone
     - 5
     - 32,856
     - 2,390
     - 16,390
     - 2.00
   * - SFH
     - B_N1
     - 5
     - 39,232
     - 1,660
     - 11,384
     - 3.45
   * - TH
     - B_N1
     - 5
     - 61,733
     - 1,410
     - 9,670
     - 6.38
   * - TH
     - B_N2
     - 5
     - 51,715
     - 1,180
     - 8,092
     - 6.39

Year-matching alone moved every ratio substantially (Apeldoorn's real gas
consumption roughly halved 2018→2024 — a genuine regional trend, not a
data artifact, confirmed by querying CBS 81528NED for both years
directly), but landed AB/MFH/TH all in a persistent ~2-7× too-high band
even with the per-dwelling fix and a larger, noise-resistant sample.
``--period`` now defaults to ``"<weather-year>JJ00"`` so the two years
can't drift apart by accident in a future run; pass ``--period``
explicitly to compare mismatched years on purpose.

**Honest state of this validation (before the two updates below)**:
useful, and has already found and fixed one real bug (the per-dwelling
normalization). What's left unexplained is no longer plausibly sample
noise or a year mismatch — both were real contributors and are now
controlled for, and a real, still-too-high gap remains.

**Update 1 (2026-08-18) — DHW/cooking modeling wired in, real effect
quantified.** ``ModelBUEM`` now models domestic hot water and gas-cooking
energy (:doc:`buildings`'s ``q_w_nd`` row, ``buem.thermal.dhw_cooking``) —
``validation`` reports a second comparison alongside the original: buem's
own ``heating_kWh + dhw_kWh + cooking_gas_kWh`` against CBS's *real,
unstripped* gas total (no more 78%/20%/2% stripping needed on the CBS
side). Run for real against the same Apeldoorn sample:

.. list-table::
   :header-rows: 1
   :widths: 12 12 8 12 12 10 12 12 10

   * - type
     - neigh
     - n
     - heat kWh
     - CBS heat
     - ratio
     - total kWh
     - CBS full
     - ratio
   * - AB
     - B_Alone
     - 5
     - 42,226
     - 6,103
     - 6.92
     - 44,456
     - 7,825
     - 5.68
   * - AB
     - B_N1
     - 4
     - 12,674
     - 6,103
     - 2.08
     - 13,935
     - 7,825
     - 1.78
   * - MFH
     - B_Alone
     - 5
     - 35,425
     - 6,103
     - 5.80
     - 41,078
     - 7,825
     - 5.25
   * - MFH
     - B_N1
     - 5
     - 40,583
     - 6,103
     - 6.65
     - 45,690
     - 7,825
     - 5.84
   * - SFH
     - B_Alone
     - 5
     - 32,856
     - 16,390
     - 2.00
     - 43,864
     - 21,013
     - 2.09
   * - SFH
     - B_N1
     - 5
     - 39,232
     - 11,384
     - 3.45
     - 50,404
     - 14,595
     - 3.45
   * - TH
     - B_N1
     - 5
     - 61,583
     - 9,670
     - 6.37
     - 73,328
     - 12,397
     - 5.92
   * - TH
     - B_N2
     - 5
     - 51,539
     - 8,092
     - 6.37
     - 63,027
     - 10,375
     - 6.08

DHW/cooking modestly improves 7 of 9 groups' ratios (~5-20% relative
reduction), leaves one flat, and slightly worsens one — real, but nowhere
near enough to close a 2-7× gap on its own (DHW+cooking are only ~22% of
a typical Dutch gas bill by CBS's own split). Confirms the gap's dominant
driver lies elsewhere.

**Update 2 (2026-08-18) — a real, substantial contributor found:
construction-year sampling skew.** ``buem.analysis.netherlands
.construction_year_stratification`` checked whether this validation's
"first N buildings in file order" sample selection is representative of
the real TABULA construction-era mix. It is not: TH's sample was 80% the
oldest, worst-insulated TABULA class (``NL.01``, wall U ≈ 5.26 W/m²K) vs.
only 21% of the real population; SFH was skewed similarly, less severely.
Simulating one real building per (type, era) stratum and weighting by
each era's real population share gives, for the same groups above:

.. list-table::
   :header-rows: 1
   :widths: 10 12 18 18 14 14 14

   * - type
     - neigh
     - sample-implied kWh/m²
     - population-weighted kWh/m²
     - skew inflation
     - reported ratio
     - skew-adjusted ratio
   * - SFH
     - B_Alone
     - 219.6
     - 197.3
     - 1.11×
     - 2.00
     - **1.80**
   * - SFH
     - B_N1
     - 219.6
     - 197.3
     - 1.11×
     - 3.45
     - **3.10**
   * - TH
     - B_N1
     - 350.4
     - 185.3
     - 1.89×
     - 6.37
     - **3.37**
   * - TH
     - B_N2
     - 276.4
     - 185.3
     - 1.49×
     - 6.37
     - **4.27**

Sampling skew toward older/worse-insulated archetypes alone accounts for
roughly half of TH's reported gap and a real chunk of SFH's — a
substantial, quantified, but not complete explanation, independent of the
DHW/cooking finding above. A genuine residual gap remains for both types.
AB/MFH (only 12 and 28 total residential buildings in the whole dataset)
are too small for the same population-share treatment — their ratios
carry a separate small-N caveat.

Candidate explanations still not investigated: whether the remaining
residual gap traces to archetype/U-value matching accuracy itself, or the
gas→heat conversion's 78%/90% assumptions running too low for this
specific housing mix. See ``.claude/dhw_cooking_heat_handoff.md`` for the
full session write-up.


Population-complete results
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Every table above is a sampled run** (3-5 buildings per group, taken in
file order) and is superseded. The sampling was the point of the skew
diagnosis immediately above; rather than continue correcting for it, the
whole population is now simulated in one pass
(``buem.analysis.batch --source csv``) and aggregated
(``validation --from-parquet``).

The current dated results -- all 3,101 residential buildings and the 742
label-matched subset, with the per-group tables and an explanation of the
two averaging conventions -- live in :doc:`../validation/loenen_cbs`.
They are kept there rather than here so each run is recorded with the
configuration that produced it, and can be compared against a later
re-run like for like.


Known open items
--------------------

See ``.claude/residential/open.md`` for the live, evolving list — as of
2026-08-18:

- A wall touching two different neighbour segments only records its
  single best-overlap party-wall match (Stage 1).
- MFH vs. AB has no published threshold; buem's own ≤4-units heuristic
  is a first pass (Stage 3).
- The label→year-class table (``LABEL_TO_YEAR_CLASS``) and the U-value
  table's own provenance for windows specifically rely on a less
  precisely-sourced reference than the wall/roof/floor Rc history (Stage 4).
