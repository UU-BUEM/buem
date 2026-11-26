# v4 — PROMOTED TO LIVE (2026-08-14)

**This is now the live, enforced BUEM-side contract** — `geojson_validator.py`
validates and converts against this shape (superseding v3). Promoted with
the user's explicit go-ahead this session, on the basis that the
EnerPlanET developer told the user (2026-08-13) they are already working
on their own v5 — i.e. EnerPlanET has itself moved past v3, so buem
finalizing v4 on its side is a reasonable, timely step rather than staying
pinned to a contract EnerPlanET is also leaving behind. **This is not the
same as EnerPlanET having reviewed and agreed to this exact file** — the
buem-side-only proposals below (`building_type`'s enum, `capacity`,
`buem.weather`) were never reconciled against EnerPlanET's real schema
line-by-line; that reconciliation, if EnerPlanET's real v5 turns out to
look different from what's promoted here, is a follow-up the user owns,
not assumed by this promotion. `versions/v3/` is retained as an archived,
deprecated snapshot per `VERSIONING.md`'s convention (see its own note).

This file keeps its original draft-era history below for the historical
record of what was proposed, why, and what was reconciled against
EnerPlanET's real schema before promotion. Two schema-documented fields
are explicitly **not yet wired up** despite being promoted along with
everything else, and are rejected (not silently ignored) if a request
sends them — see "Deliberately unwired at promotion" at the bottom of
this file: `weather.use_percentile`/`percentile`, and
`solver.compute_cooling`.

**Correction (2026-08-06): this note previously said "v3 there is current" —
that's now known stale.** EnerPlanET's repo has its own real `schemas/v4/`
today (confirmed by fetching the actual raw JSON, not just browsing).
Whether that means v4 is *live* on their end, or is their own
not-yet-deployed draft mirroring this folder's own status, is not known
from here — only the user's direct conversation with the EnerPlanET
developer can settle that. Treat "which version EnerPlanET actually runs
in production" as an open question, not settled by this file — the
promotion above is buem finalizing its *own* side, not a claim that
EnerPlanET has adopted it.

## What's in this draft

Several changes so far. The first two are additive/services-building
related, accumulated while wiring up `ServiceBuildingProfile` support in
buem. The third and fourth are part of making the `weather` package a
compulsory, real-fetch-only data source (buem has no bundled/local-file
weather fallback anymore -- see buem's own `CLAUDE.md`). The last three
(`building.equipment`, `buem.weather.profile`, and the
`electricity_load_profile` description update) were added together
2026-08-14, alongside EnerPlanET's original four asks for optional
electricity-profile/building-categorization/LOD3/equipment/weather-profile
input -- see `.claude/occupancy_module_activities.md` for the occupancy-side
asks that same pass surfaced:

- `building.building_type` gets an `enum` (previously free-text): the 4
  TABULA residential codes (`SFH`/`MFH`/`TH`/`AB`) plus 8 occupancy
  service-building type ids (`supermarket`/`office`/`restaurant`/`school`/
  `hotel`/`bakery`/`warehouse`/`clinic`). Per `VERSIONING.md`'s own semver
  rules this is a **MAJOR** change ("validation constraints become
  stricter"), not a MINOR addition — a client sending a `building_type`
  value outside this set, which previously passed, would now be rejected.
- `building.capacity` (new optional integer field, service-building
  occupancy sizing) — this one alone would be MINOR (backward-compatible
  addition).
- New `buem.weather` object (sibling to `building`/`solver`): optional
  `provider` (`era5-land`/`cosmo-rea6`/`merra-2`, default `merra-2` as of
  2026-08-04 -- era5-land was the original default but currently fails at
  buem's own default test cell, see CLAUDE.md) and `year` (defaults to
  `start_time`'s calendar year) selecting the real per-location weather-
  module fetch. Additive/MINOR. The equivalent v2-format fields
  (`building_attributes.year`/`weather_provider`) are already live in
  `geojson_validator.py`'s `BuildingAttributesSchema` (not gated behind this
  draft — that schema isn't part of the versioned contract). **Not yet
  reachable from a real v3 (live) request**: `geojson_validator.py`'s
  `_convert_v3_to_v2()` doesn't read any `weather`-equivalent block from
  v3 input yet, so today only true v2-format requests (or direct
  `AttributeBuilder` calls) can set `year`/`weather_provider` -- see
  CLAUDE.md "Weather is compulsory" for what extending that would need.
- `buem.weather` gains `use_percentile` (boolean, default `false`) and
  `percentile` (enum `P10`/`P50`/`P90`, default `P50`) (2026-08-06).
  Alternative to `year`: instead of a specific calendar year, select a
  percentile climate year -- ranked across the provider's available
  archive years at this location -- computed via `weather`'s existing
  per-provider percentile infrastructure (`percentile_index.py`, already
  present for all three providers, unrelated to this schema change).
  `year` is ignored when `use_percentile` is true. Both optional with
  defaults, so additive/MINOR. **Schema-only, same starting point as
  `buem.weather` itself**: not read by `_convert_v3_to_v2()`, not wired
  through `AttributeBuilder`/the weather fetch itself, and not exposed by
  the point-query HTTP API scaffold (`UU-BUEM/weather`'s
  `src/weather/api/`, GET `/v1/weather/point` takes a specific `year`
  only, no percentile parameter yet) -- three separate, unstarted wiring
  steps, each its own later task.
- `building.num_persons`, `building.archetype` (new optional fields, both
  additive/MINOR) (2026-08-07), alongside the existing `building.capacity`
  -- part of making `occupancy` a compulsory dependency (buem's own
  `CLAUDE.md`, "Occupancy is compulsory"). `num_persons` mirrors
  `AttributeBuilder`'s existing runtime support; `archetype` is new,
  selecting one of occupancy's registered household archetypes as an
  explicit override of `cfg_attribute.DEFAULT_ARCHETYPE_BY_BUILDING_TYPE`'s
  first-pass `building_type`-based default. **Deliberately excludes
  `seed`**: an internal RNG-reproducibility knob, not a modeling input a
  client should set -- see `.claude/occupancy_gains_handoff.md`'s
  seed-ownership note for the reasoning and the proposal that `occupancy`
  itself own a deterministic default instead. **`num_persons`/`archetype`
  now reach a real v3 (live) request too** (2026-08-07):
  `_convert_v3_to_v2()` forwards `capacity`/`num_persons`/`archetype`
  (not `seed`) from `building` into `building_attributes` -- see
  `.claude/occupancy_gains_handoff.md` for the full writeup. This does
  *not* mean EnerPlanET's actual v3 contract documents or has agreed to
  these fields (it hasn't) -- only that buem's own runtime happens to
  read them if a v3-format client sends them, same caveat as `year`/
  `weather_provider` before it.
- `building.has_ev`, `building.ev_ownership_probability`,
  `building.ev_charger_kw` (new optional fields, additive/MINOR,
  2026-11-23). occupancy now models the household electric vehicle as its
  own sub-model (trips, state of charge, plug-in behaviour) rather than as
  an appliance row, and keeps its charging out of the internal-gain
  electricity load. `equipment.ev_charger` is removed from the equipment
  property list accordingly (a field removal, but only from this unagreed
  draft); buem's runtime still accepts it from older payloads and maps it
  to `has_ev` with a deprecation warning.
- `building.has_pv`, `building.pv_capacity_kwp`, `building.has_battery`,
  `building.battery_capacity_kwh` (new optional fields, additive/MINOR,
  2026-11-24). Existing on-site installations: a rooftop PV system and a
  stationary home battery. Building facts only: buem is a demand model
  and computes neither PV output nor battery operation (PV generation is
  conversion-technologies', consistent with EnerPlanET's pysam-pv), and
  neither changes the thermal results. A capacity without its flag
  implies the flag. `geojson_validator.py` forwards these and the
  `has_ev` fields from a request's `building` object (user sign-off
  2026-11-24); a v3 request carrying them is forwarded too, although the
  live v3 schema does not list them.
- `building.equipment` (new optional field, additive/MINOR, 2026-08-14,
  **reshaped 2026-08-14 same day** from an initial `{mode: "include"|
  "exclude", items: [...]}` draft to a per-item boolean map --
  `{washing_machine: true, oven: false, ...}` -- closer to the original
  "inclusion/exclusion boolean" request, more expressive (mixes forced
  include and forced exclude in one request, which a single global `mode`
  couldn't), and lets JSON Schema reject an unrecognized id itself via
  `additionalProperties: false` rather than only at runtime). Forwarded to
  `occupancy.ElectricityConsumptionProfile`'s `equipment=` override (added
  in occupancy v3.1.0, previously unused by buem): `true` sets that item's
  `ownership_probability` to 1.0 (guaranteed owned, not just more likely);
  `false` omits it from the table entirely (guaranteed excluded); an
  omitted id is left exactly as occupancy's own archetype-adjusted default
  produces it (`AttributeBuilder._resolve_equipment_table()` reads this
  base table via `ElectricityConsumptionProfile.get_equipment_table()`,
  not the raw `default_equipment_table()`, specifically so per-archetype
  `equipment_overrides` aren't silently lost for unmentioned items).
  Residential `building_type` only -- ignored, with a logged warning, for
  service-building types, since `occupancy.ServiceBuildingProfile` has no
  per-item equipment selection yet (a real occupancy-side gap, not a
  buem limitation -- see `.claude/occupancy_module_activities.md`). The
  property list is hand-copied from occupancy's `households/data/
  equipment.json` -- occupancy has no stable top-level export for this
  registry yet, the same caveat `building_type`'s enum already carries
  for `SERVICE_BUILDING_TYPES` pre-promotion (fixed there in v3.1.0;
  requested for equipment too in `.claude/occupancy_module_activities.md`
  item 1). **Live as of the v3->v4 promotion (2026-08-14)**:
  `_convert_v3_to_v2()`'s successor forwards this field from a real
  request -- no longer a deferred tier-1 step.
- `buem.weather.profile` (new optional field, additive/MINOR, 2026-08-14)
  -- caller-supplied T/GHI/DHI/DNI weather timeseries, referenced by file
  path, mirroring `buem.inputs.electricity_load_profile`'s existing
  path/format pattern. When provided, used directly instead of fetching
  from a weather-module archive; `provider`/`year`/`use_percentile`/
  `percentile` are ignored. The buem-internal equivalent
  (`AttributeBuilder.generate_weather_profile()`'s `use_provided_weather`
  flag, and `CfgBuilding.WeatherConfig`'s DataFrame/dict acceptance)
  already existed before this schema field and needed no change --
  `_convert_v3_to_v2()` reading this block from a real v3 request is the
  only remaining (deferred, tier-1) step, same as `buem.weather.provider`/
  `year` above.
- **Azimuth/tilt inheritance and LOD3-optional synthesis made visible in
  the schema itself** (2026-08-14, docs-only, no field/shape change):
  `envelope_element.azimuth`/`tilt`/`parent_id` and the `envelope`
  object's own description now state, in the contract text EnerPlanET
  actually reads, that a window/door's azimuth/tilt are always forced to
  match its `parent_id`-referenced surface (correcting a mismatch rather
  than rejecting it), and that omitted window/door/ventilation elements
  are synthesized internally rather than left empty. Previously this
  behavior only lived in Python docstrings/`buildings.rst`, invisible to
  anyone reading just the JSON schema.
- `buem.inputs.electricity_load_profile`'s description updated
  (2026-08-14, no field/shape change) to note that the caller-supplied
  profile is now substituted via `occupancy.to_buem_profiles(elec_load=
  ...)` rather than bypassing occupancy generation entirely --
  `Q_ig`/`occ_nothome`/`occ_sleeping` still come from a real occupancy
  generation. Previously (pre-2026-08-14) buem's internal
  `use_provided_elecLoad` flag skipped calling occupancy altogether,
  losing those three too; this field's schema shape is unchanged, only
  buem's internal handling of it improved to match what occupancy itself
  actually supports.
- **Per-provider weather year-range tightening** (2026-08-14, per
  consultation): `weather_source.provider`/`.year` descriptions now state
  each provider's real archive coverage (`era5-land` 1980-2025,
  `cosmo-rea6` 1995-2018, `merra-2` 1950-2025), and `weather_source`
  gained `allOf`/`if`/`then` blocks enforcing the matching range per
  provider declaratively (the real enforcement is in
  `geojson_validator.py`; this is the same value expressed for any other
  consumer validating directly against the schema file). `year`'s own
  flat bound loosened from the old placeholder `1940-2100` to `1950-2025`
  -- the true union across all three providers.
- **`weather_source.profile.format` gains `json`, now the default**
  (2026-08-14, confirmed as EnerPlanET's actual format): an array of
  hourly records, each `{time, T, GHI, DHI, DNI}`. `csv`/`parquet` remain
  accepted for other/internal callers -- `profile_file_loader
  .load_weather_profile()` supports all three.
- **All remaining "DRAFT"/"not yet agreed"/"not yet reconciled" language
  removed from `request_schema.json`/`response_schema.json`** (2026-08-14,
  following the promotion above -- these had gone stale the moment
  `geojson_validator.py` started enforcing this shape). Buem-side-only
  proposals (`building_type`'s enum, `capacity`, the `buem.weather`
  object, `equipment`) now read "buem-side proposal, live as of the v4
  promotion" instead, still noting where each remains absent from
  EnerPlanET's own real v4 as of the last check.

## Reconciliation against EnerPlanET's real v4 (2026-08-06)

Fetched the actual raw JSON from
[enerplanet/buem-gateway/schemas/v4](https://github.com/enerplanet/buem-gateway/tree/main/schemas/v4)
(all four files: both schemas, both examples) and diffed against this
folder. They'd diverged in both directions. **All of buem's own
pre-existing additions below were kept as-is, not removed** — reconciling
means adding what EnerPlanET has that we didn't, and fixing one real bug,
not discarding buem-side proposals:

**Added here, from EnerPlanET's real v4** (previously missing entirely):

- `building.name` / `envelope_element.name` — display-only labels.
- `building.envelope` is no longer `required` — when omitted, the model
  derives surfaces/U-values from the TABULA variant identified by
  `building_type` + `construction_period` + `country`.
- `construction_period`'s description corrected: it's a TABULA
  construction-year *class code* (e.g. `"04"`), not a literal year range
  — buem's own example data still uses the older `"1965-1974"`-style
  range pending a check of what buem's actual TABULA lookup code parses;
  not changed, since guessing at internal engine behavior here would risk
  a wrong-but-plausible example. Flagged, not fixed.
- `solver.compute_cooling` (bool, default `false`) — makes the cooling
  constraint/output conditional. Response side updated to match:
  `load_summary`/`load_timeseries` now only require `cooling` when this
  was true (previously unconditionally required in both request and
  response); `model_metadata` gained `simulations_run` and
  `electricity_source`.
- New `buem.inputs.electricity_load_profile` object (client-supplied
  electricity timeseries override, with `path`/`unit`) — request and
  response (echoed) both updated.
- Defaults added to `phi_int` (3.0 W/m2) and `q_w_nd` (12.5 kWh/(m2.a)),
  matching EnerPlanET's real values.

**A real bug fixed, not just a gap**: `q_w_nd`'s unit enum was
`"kWh/(m2a)"` (no dot) — EnerPlanET's real v4 uses `"kWh/(m2.a)"` (with a
dot). A genuine EnerPlanET request using their own documented unit string
would have failed validation against this draft. Now matches theirs
exactly.

**Kept, still buem-side-only proposals** (confirmed absent from
EnerPlanET's real v4 as of this check — each field's own description now
says so explicitly): `building_type`'s `enum` (theirs is still free-text
— per `VERSIONING.md` this remains the one MAJOR/breaking change in this
draft), `building.capacity`, and the entire `buem.weather` object
(`provider`/`year`/`use_percentile`/`percentile` — EnerPlanET's v4
resolves weather from `geometry.coordinates` alone, no client-specified
provider/year/percentile at all). None of these were proposed to
EnerPlanET before this draft existed, so their absence there is expected,
not a discovered gap — nothing to "fix" here, just now documented
precisely rather than asserted.

All four files (`request_schema.json`, `response_schema.json`,
`example_request.json`, `example_response.json`) still validate
internally (`jsonschema.validate()` against their own examples) after
this pass.

## Status: PROMOTED (2026-08-14) -- this folder is now tier 1

**This folder is no longer freely editable per CLAUDE.md's old tier-2
framing.** Now that `geojson_validator.py` actually loads/enforces this
shape (via the constants in `building_registry.py` and the conversion
logic in `_convert_v3_to_v2`), `versions/v4/*.json` sits alongside
`geojson_validator.py` itself as the live, agreed-with-the-user contract
— any further change here needs the same explicit check-in
`versions/v3/` always required, not the old "no need to check in per
change" latitude. `versions/v3/` is retained as an archived, deprecated
snapshot (matching how `v1`/`v2` are already treated per `VERSIONING.md`)
-- not deleted, kept for historical/rollback reference.

Not formally proposed to or reviewed by EnerPlanET line-by-line -- see
the promotion note at the top of this file for the reasoning (EnerPlanET
already moving to their own v5) and its limits (this is buem finalizing
its own side, not a claim of EnerPlanET sign-off on every field here).

### Deliberately unwired at promotion

Two schema-documented fields were promoted along with everything else
but are **explicitly rejected**, not silently accepted-and-ignored, if a
request supplies them -- `geojson_validator.py::_validate_single_feature`
adds a validation error naming the field. Both need real, separate
implementation work before they can be wired for real:

- `weather.use_percentile` / `percentile` -- needs weather's own
  percentile-year infrastructure (`percentile_index.py`) wired through
  the point-query HTTP API or local-archive path, neither of which
  exposes a percentile parameter today.
- `solver.compute_cooling` -- needs the cooling upper-bound constraint in
  `ModelBUEM._init5R1C`'s dead-band LP to become conditional (today both
  heating and cooling are always computed and returned unconditionally,
  confirmed against the real solver code before deciding to defer this
  rather than rush a change to the model's numerical core in the same
  pass as the schema/validation work above).

Also **not** promoted: `building.envelope` becoming non-required (line
~80 above) -- the corresponding "derive Walls/Roof/Floor from a matched
TABULA variant when envelope is entirely absent" capability doesn't
exist in `live_synthesis.py` (only Windows/Doors/Ventilation synthesis
*from already-supplied* Walls geometry is implemented). `envelope` with
at least one element remains required, matching today's actual behavior
-- `require_v2_or_v3` in `geojson_validator.py` is unchanged.
