"""
Self-contained HTML report for a weather-provider comparison run.

Reuses the exact visual design of the one-off report this analysis package
grew out of (published as a Claude Artifact, "Three Reanalyses, One Roof")
-- rebuilt here as a real, versioned, importable function so the user can
regenerate it locally without going through the Artifact tool. The output
is a single ``.html`` file with everything inlined (no CDN scripts, no
external stylesheets/fonts) -- open it directly in a browser.

Extends the original design with a second chart set answering "check T,
GHI, DNI, and GHI difference between models": per-provider monthly T/GHI/
DNI/DHI means plus a pairwise GHI-difference table, driven by
``summarize.summarize_weather()``.
"""
from __future__ import annotations

import json
from pathlib import Path

from buem.analysis.building_selection import wall_exposure
from buem.analysis.provider_comparison import ProviderRunResult
from buem.analysis.summarize import DemandSummary, WeatherSummary
from buem.buildings.building import Building

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Fixed provider -> (light-hex, dark-hex), matching the original report.
# Assigned by identity, never cycled/reassigned when only a subset of
# providers is run -- so a given provider always reads as the same color
# across different reports.
_PROVIDER_COLORS: dict[str, tuple[str, str]] = {
    "merra-2": ("#2a78d6", "#3987e5"),
    "cosmo-rea6": ("#eb6834", "#d95926"),
    "era5-land": ("#1baf7a", "#199e70"),
}
_PROVIDER_LABELS: dict[str, str] = {
    "merra-2": "MERRA-2",
    "cosmo-rea6": "COSMO-REA6",
    "era5-land": "ERA5-Land",
}
# Safety net for a provider outside the three known reanalyses -- kept
# visually distinct from them, not run through the dataviz palette
# validator since it only ever covers an ad hoc 4th/5th series, not a
# designed part of this report.
_FALLBACK_COLORS: tuple[tuple[str, str], ...] = (
    ("#9b59b6", "#b47ddb"),
    ("#c0392b", "#e0564a"),
)


def _color_for(provider: str, index: int) -> tuple[str, str]:
    return _PROVIDER_COLORS.get(provider, _FALLBACK_COLORS[index % len(_FALLBACK_COLORS)])


def _label_for(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, provider)


def build_html_report(
    building: Building,
    results: dict[str, ProviderRunResult],
    demand: DemandSummary,
    weather: WeatherSummary,
    *,
    location_label: str,
    year: int,
    output_path: str | Path,
) -> Path:
    """Render a self-contained HTML comparison report to *output_path*.

    Parameters
    ----------
    building : Building
        The mapped building the run used (for the meta-strip: type,
        construction period, floor area, wall exposure, comfort setpoints).
    results : dict[str, ProviderRunResult]
        Per-provider run output, e.g. from
        ``provider_comparison.run_building_across_providers()``.
    demand : DemandSummary
        From ``summarize.summarize_demand(results)``.
    weather : WeatherSummary
        From ``summarize.summarize_weather(results)``.
    location_label, year
        Shown in the meta-strip; not derived from *results* since a caller
        may want a human-readable place name rather than raw coordinates.
    output_path : str or Path
        Where to write the ``.html`` file. Parent directories are created
        if needed.

    Returns
    -------
    Path
        *output_path*, resolved.
    """
    providers = list(results)
    if not providers:
        raise ValueError("build_html_report: results is empty -- nothing to report.")

    colors = {p: _color_for(p, i) for i, p in enumerate(providers)}
    labels = {p: _label_for(p) for p in providers}

    first = results[providers[0]]
    exposure = wall_exposure(building)

    heating_monthly = {p: demand.monthly_kWh["heating_kWh"][p].round(0).astype(int).tolist() for p in providers}
    cooling_monthly = {p: demand.monthly_kWh["cooling_kWh"][p].round(0).astype(int).tolist() for p in providers}
    elec_monthly = {p: demand.monthly_kWh["elec_kWh"][p].round(0).astype(int).tolist() for p in providers}

    annual = {
        p: {
            "heating": round(float(demand.annual_kWh.loc[p, "heating_kWh"]), 1),
            "cooling": round(float(demand.annual_kWh.loc[p, "cooling_kWh"]), 1),
            "elec": round(float(demand.annual_kWh.loc[p, "elec_kWh"]), 1),
        }
        for p in providers
    }

    t_monthly = {p: weather.monthly_mean["T"][p].round(2).tolist() for p in providers}
    ghi_monthly = {p: weather.monthly_mean["GHI"][p].round(1).tolist() for p in providers}
    dni_monthly = {p: weather.monthly_mean["DNI"][p].round(1).tolist() for p in providers}
    dhi_monthly = {p: weather.monthly_mean["DHI"][p].round(1).tolist() for p in providers}

    weather_annual = {
        p: {
            "T_mean": round(float(weather.annual_stats.loc[p, "T_mean_degC"]), 2),
            "T_min": round(float(weather.annual_stats.loc[p, "T_min_degC"]), 2),
            "T_max": round(float(weather.annual_stats.loc[p, "T_max_degC"]), 2),
            "GHI_sum": round(float(weather.annual_stats.loc[p, "GHI_sum_kWh_m2"]), 1),
            "DNI_sum": round(float(weather.annual_stats.loc[p, "DNI_sum_kWh_m2"]), 1),
            "DHI_sum": round(float(weather.annual_stats.loc[p, "DHI_sum_kWh_m2"]), 1),
        }
        for p in providers
    }

    ghi_diff_rows = [
        {
            "pair": pair,
            "mean_abs_diff": round(float(row["mean_abs_hourly_diff_Wm2"]), 2),
            "pct_diff": round(float(row["annual_total_pct_diff"]), 2),
        }
        for pair, row in weather.ghi_pairwise_diff.iterrows()
    ]

    payload = {
        "providers": [
            {"key": p, "label": labels[p], "colorLight": colors[p][0], "colorDark": colors[p][1]}
            for p in providers
        ],
        "months": MONTHS,
        "heatingMonthly": heating_monthly,
        "coolingMonthly": cooling_monthly,
        "elecMonthly": elec_monthly,
        "annual": annual,
        "tMonthly": t_monthly,
        "ghiMonthly": ghi_monthly,
        "dniMonthly": dni_monthly,
        "dhiMonthly": dhi_monthly,
        "weatherAnnual": weather_annual,
        "ghiDiffRows": ghi_diff_rows,
    }

    meta = {
        "building_id": str(building.identity.building_feature_id),
        "building_type": building.identity.building_type,
        "construction_period": building.identity.construction_period or "—",
        "A_ref": round(building.computed_A_ref(), 1),
        "wall_exposure": f"{exposure.n_exposed}/{exposure.n_walls} walls exposed",
        "comfort_range": f"{building.thermal.comfortT_lb:.0f}–{building.thermal.comfortT_ub:.0f}°C",
        "location_label": location_label,
        "year": year,
        "n_providers": len(providers),
        "provider_list": ", ".join(labels[p] for p in providers),
        "building_type_label": first.building_type or building.identity.building_type,
    }

    html = _render(meta, payload)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _render(meta: dict, payload: dict) -> str:
    payload_json = json.dumps(payload)
    return _TEMPLATE.format(
        title=f"Weather Provider Comparison — building {meta['building_id']}",
        building_id=meta["building_id"],
        building_type=meta["building_type"],
        construction_period=meta["construction_period"],
        A_ref=meta["A_ref"],
        wall_exposure=meta["wall_exposure"],
        location_label=meta["location_label"],
        year=meta["year"],
        comfort_range=meta["comfort_range"],
        provider_list=meta["provider_list"],
        n_providers=meta["n_providers"],
        payload_json=payload_json,
    )


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  .viz-root {{
    color-scheme: light;
    --bg:            #f9f9f7;
    --surface:       #fcfcfb;
    --ink:           #0b0b0b;
    --ink-secondary: #52514e;
    --ink-muted:     #898781;
    --grid:          #e1e0d9;
    --axis:          #c3c2b7;
    --border:        rgba(11,11,11,0.10);
    --accent:        #2a78d6;
    --tooltip-bg:    #0b0b0b;
    --tooltip-ink:   #fcfcfb;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) .viz-root {{
      color-scheme: dark;
      --bg:            #0d0d0d;
      --surface:       #1a1a19;
      --ink:           #ffffff;
      --ink-secondary: #c3c2b7;
      --ink-muted:     #898781;
      --grid:          #2c2c2a;
      --axis:          #383835;
      --border:        rgba(255,255,255,0.10);
      --accent:        #3987e5;
      --tooltip-bg:    #fcfcfb;
      --tooltip-ink:   #0b0b0b;
    }}
  }}
  :root[data-theme="dark"] .viz-root {{
    color-scheme: dark;
    --bg:            #0d0d0d;
    --surface:       #1a1a19;
    --ink:           #ffffff;
    --ink-secondary: #c3c2b7;
    --ink-muted:     #898781;
    --grid:          #2c2c2a;
    --axis:          #383835;
    --border:        rgba(255,255,255,0.10);
    --accent:        #3987e5;
    --tooltip-bg:    #fcfcfb;
    --tooltip-ink:   #0b0b0b;
  }}

  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  .viz-root {{
    background: var(--bg);
    color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    line-height: 1.5;
    padding: 0 0 64px;
  }}
  .mono {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", "Consolas", monospace; }}
  .shell {{ max-width: 1040px; margin: 0 auto; padding: 56px 28px 0; }}

  .eyebrow {{
    font-family: ui-monospace, "SF Mono", "Cascadia Code", "Consolas", monospace;
    font-size: 12.5px; letter-spacing: 0.09em; text-transform: uppercase;
    color: var(--accent); margin: 0 0 14px;
  }}
  h1 {{ font-size: clamp(28px, 4vw, 40px); line-height: 1.12; letter-spacing: -0.015em; margin: 0 0 16px; text-wrap: balance; max-width: 20ch; }}
  .dek {{ font-size: 16px; color: var(--ink-secondary); max-width: 66ch; margin: 0 0 28px; }}
  .meta-strip {{
    display: flex; flex-wrap: wrap; gap: 10px 28px; padding: 18px 0 32px;
    border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); margin-bottom: 40px;
  }}
  .meta-item {{ font-size: 13px; }}
  .meta-item dt {{ color: var(--ink-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; margin: 0 0 3px; }}
  .meta-item dd {{ margin: 0; font-family: ui-monospace, "SF Mono", "Cascadia Code", "Consolas", monospace; color: var(--ink); }}

  section {{ margin-bottom: 52px; }}
  .section-head {{ margin-bottom: 20px; }}
  h2 {{ font-size: 20px; letter-spacing: -0.01em; margin: 0 0 6px; }}
  .section-sub {{ font-size: 14px; color: var(--ink-secondary); max-width: 66ch; margin: 0; }}

  .callout {{
    background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--accent);
    border-radius: 4px; padding: 18px 22px; font-size: 14px; color: var(--ink-secondary); margin-bottom: 44px;
  }}
  .callout strong {{ color: var(--ink); font-weight: 600; }}
  .callout p {{ margin: 0 0 10px; }}
  .callout p:last-child {{ margin-bottom: 0; }}

  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }}
  .stat-card {{ background: var(--surface); padding: 22px 20px; }}
  .stat-provider {{ display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; margin-bottom: 16px; }}
  .swatch {{ width: 10px; height: 10px; border-radius: 2px; flex: none; }}
  .stat-row {{ display: flex; justify-content: space-between; align-items: baseline; padding: 8px 0; border-top: 1px solid var(--border); }}
  .stat-row:first-of-type {{ border-top: none; }}
  .stat-label {{ font-size: 12.5px; color: var(--ink-muted); }}
  .stat-value {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", "Consolas", monospace; font-variant-numeric: tabular-nums; font-size: 15px; font-weight: 500; }}
  .stat-value .unit {{ color: var(--ink-muted); font-size: 11px; margin-left: 3px; font-weight: 400; }}

  .chart-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 24px 24px 16px; }}
  .chart-legend {{ display: flex; flex-wrap: wrap; gap: 20px; margin-bottom: 4px; font-size: 12.5px; color: var(--ink-secondary); }}
  .legend-item {{ display: flex; align-items: center; gap: 7px; }}
  .legend-line {{ width: 16px; height: 2px; border-radius: 1px; }}
  .chart-wrap {{ position: relative; }}
  svg.chart {{ width: 100%; height: auto; display: block; overflow: visible; }}
  .gridline {{ stroke: var(--grid); stroke-width: 1; }}
  .axis-line {{ stroke: var(--axis); stroke-width: 1; }}
  .axis-label {{ fill: var(--ink-muted); font-size: 10.5px; font-family: ui-monospace, "SF Mono", "Cascadia Code", "Consolas", monospace; }}
  .series-line {{ fill: none; stroke-width: 2; }}
  .hover-dot {{ r: 3.5; stroke: var(--surface); stroke-width: 1.5; }}
  .crosshair {{ stroke: var(--ink-muted); stroke-width: 1; stroke-dasharray: 2 3; opacity: 0; }}

  .chart-tooltip {{
    position: absolute; pointer-events: none; background: var(--tooltip-bg); color: var(--tooltip-ink);
    border-radius: 5px; padding: 10px 12px; font-size: 12px; line-height: 1.5; opacity: 0;
    transition: opacity 0.08s ease; transform: translate(-50%, -100%); white-space: nowrap; z-index: 5;
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
  }}
  .chart-tooltip .tt-month {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", "Consolas", monospace; font-weight: 600; margin-bottom: 6px; letter-spacing: 0.03em; }}
  .chart-tooltip .tt-row {{ display: flex; align-items: center; gap: 6px; justify-content: space-between; }}
  .chart-tooltip .tt-dot {{ width: 7px; height: 7px; border-radius: 2px; flex: none; }}
  .chart-tooltip .tt-val {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", "Consolas", monospace; margin-left: 10px; font-variant-numeric: tabular-nums; }}

  .table-scroll {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 6px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; background: var(--surface); min-width: 560px; }}
  th, td {{ padding: 9px 14px; text-align: right; border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; font-family: ui-monospace, "SF Mono", "Cascadia Code", "Consolas", monospace; }}
  th:first-child, td:first-child {{ text-align: left; font-family: system-ui, sans-serif; }}
  thead th {{ font-family: system-ui, sans-serif; font-weight: 600; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--ink-muted); background: var(--bg); }}
  tbody tr:last-child td {{ border-bottom: none; }}
  tbody tr:hover td {{ background: var(--bg); }}

  footer {{ max-width: 1040px; margin: 0 auto; padding: 32px 28px 0; border-top: 1px solid var(--border); font-size: 12.5px; color: var(--ink-muted); }}
  footer p {{ max-width: 74ch; margin: 0 0 8px; }}

  @media (max-width: 640px) {{
    .stat-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="viz-root">
  <div class="shell">

    <p class="eyebrow">BuEM &middot; 5R1C thermal model</p>
    <h1>Weather provider comparison</h1>
    <p class="dek">
      One building, one year, {n_providers} weather source(s) ({provider_list}) run
      hour-by-hour through buem's own simulation pipeline -- to see how much the
      choice of weather source alone moves a heating, cooling, and electricity
      estimate, and how much the underlying T/GHI/DNI/DHI inputs themselves differ.
    </p>

    <dl class="meta-strip">
      <div class="meta-item"><dt>Building</dt><dd>{building_type} &middot; {construction_period} &middot; {A_ref} m&sup2;</dd></div>
      <div class="meta-item"><dt>Source</dt><dd>id {building_id}</dd></div>
      <div class="meta-item"><dt>Envelope</dt><dd>{wall_exposure}</dd></div>
      <div class="meta-item"><dt>Location</dt><dd>{location_label}</dd></div>
      <div class="meta-item"><dt>Year</dt><dd>{year}</dd></div>
      <div class="meta-item"><dt>Setpoints</dt><dd>{comfort_range}, constant</dd></div>
    </dl>

    <section>
      <div class="section-head">
        <h2>Annual totals</h2>
        <p class="section-sub">Same building, same year &mdash; the only variable is which weather source fed the model.</p>
      </div>
      <div class="stat-grid" id="stat-grid"></div>
    </section>

    <section>
      <div class="section-head">
        <h2>Heating demand by month</h2>
      </div>
      <div class="chart-card">
        <div class="chart-legend" id="legend-heating"></div>
        <div class="chart-wrap" id="chart-heating"></div>
      </div>
    </section>

    <section>
      <div class="section-head">
        <h2>Cooling demand by month</h2>
      </div>
      <div class="chart-card">
        <div class="chart-legend" id="legend-cooling"></div>
        <div class="chart-wrap" id="chart-cooling"></div>
      </div>
    </section>

    <section>
      <div class="section-head">
        <h2>Weather inputs: temperature</h2>
        <p class="section-sub">Monthly mean 2 m air temperature per provider.</p>
      </div>
      <div class="chart-card">
        <div class="chart-legend" id="legend-t"></div>
        <div class="chart-wrap" id="chart-t"></div>
      </div>
    </section>

    <section>
      <div class="section-head">
        <h2>Weather inputs: irradiance</h2>
        <p class="section-sub">Monthly mean global horizontal irradiance (GHI), W/m&sup2;.</p>
      </div>
      <div class="chart-card">
        <div class="chart-legend" id="legend-ghi"></div>
        <div class="chart-wrap" id="chart-ghi"></div>
      </div>
    </section>

    <section>
      <div class="section-head">
        <h2>GHI difference between providers</h2>
        <p class="section-sub">Mean absolute hourly difference and annual-total percent difference, every pair of providers run.</p>
      </div>
      <div class="table-scroll">
        <table id="ghi-diff-table"></table>
      </div>
    </section>

    <section style="margin-bottom: 8px;">
      <div class="section-head">
        <h2>Monthly figures</h2>
      </div>
      <div class="table-scroll">
        <table id="data-table"></table>
      </div>
    </section>

  </div>

  <footer>
    <p>Simulation: BuEM 5R1C model (ISO&nbsp;13790). Weather: hourly T/GHI/DHI/DNI at the
    nearest grid cell, local processed archives (<span class="mono">buem.analysis.weather_providers</span>).
    Cooling figures shown as positive magnitude; the model's own sign convention is negative.
    Generated by <span class="mono">buem.analysis.report.build_html_report()</span> -- rerun any time
    with fresh data via the same function.</p>
  </footer>
</div>

<script>
(function () {{
  const DATA = {payload_json};
  const months = DATA.months;
  const providers = DATA.providers;

  const root = document.querySelector(".viz-root");
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const explicitTheme = document.documentElement.getAttribute("data-theme");
  const isDark = explicitTheme === "dark" || (explicitTheme !== "light" && prefersDark);
  function colorOf(p) {{ return isDark ? p.colorDark : p.colorLight; }}

  // ── Stat cards ──────────────────────────────────────────────────
  const statGrid = document.getElementById("stat-grid");
  providers.forEach(p => {{
    const a = DATA.annual[p.key];
    const card = document.createElement("div");
    card.className = "stat-card";
    card.innerHTML = `
      <div class="stat-provider">
        <span class="swatch" style="background:${{colorOf(p)}}"></span>
        ${{p.label}}
      </div>
      <div class="stat-row">
        <span class="stat-label">Heating</span>
        <span class="stat-value">${{a.heating.toLocaleString(undefined,{{maximumFractionDigits:0}})}}<span class="unit">kWh</span></span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Cooling</span>
        <span class="stat-value">${{a.cooling.toLocaleString(undefined,{{maximumFractionDigits:0}})}}<span class="unit">kWh</span></span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Electricity</span>
        <span class="stat-value">${{a.elec.toLocaleString(undefined,{{maximumFractionDigits:0}})}}<span class="unit">kWh</span></span>
      </div>
    `;
    statGrid.appendChild(card);
  }});

  function buildLegend(elId) {{
    const el = document.getElementById(elId);
    providers.forEach(p => {{
      const item = document.createElement("div");
      item.className = "legend-item";
      item.innerHTML = `<span class="legend-line" style="background:${{colorOf(p)}}"></span>${{p.label}}`;
      el.appendChild(item);
    }});
  }}
  buildLegend("legend-heating");
  buildLegend("legend-cooling");
  buildLegend("legend-t");
  buildLegend("legend-ghi");

  function niceMax(v) {{
    if (v <= 0) return 1;
    const mag = Math.pow(10, Math.floor(Math.log10(v)));
    const norm = v / mag;
    let step;
    if (norm <= 1) step = 1; else if (norm <= 2) step = 2; else if (norm <= 5) step = 5; else step = 10;
    return step * mag;
  }}

  function buildChart(containerId, dataByProvider, unitLabel, decimals) {{
    const container = document.getElementById(containerId);
    const W = 900, H = 300;
    const M = {{ top: 16, right: 20, bottom: 30, left: 56 }};
    const plotW = W - M.left - M.right;
    const plotH = H - M.top - M.bottom;

    let maxV = 0, minV = 0;
    providers.forEach(p => {{
      dataByProvider[p.key].forEach(v => {{ if (v > maxV) maxV = v; if (v < minV) minV = v; }});
    }});
    const yMax = niceMax(maxV * 1.15) || 1;
    const yMin = minV < 0 ? -niceMax(-minV * 1.15) : 0;
    const yStep = niceMax((yMax - yMin) / 5) || 1;

    const xFor = (i) => M.left + (plotW * i) / (months.length - 1);
    const yFor = (v) => M.top + plotH - (plotH * (v - yMin)) / (yMax - yMin);

    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", `0 0 ${{W}} ${{H}}`);
    svg.setAttribute("class", "chart");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `Monthly ${{unitLabel}} by weather provider`);

    for (let v = yMin; v <= yMax + 1e-9; v += yStep) {{
      const y = yFor(v);
      const gl = document.createElementNS(svgNS, "line");
      gl.setAttribute("x1", M.left); gl.setAttribute("x2", W - M.right);
      gl.setAttribute("y1", y); gl.setAttribute("y2", y);
      gl.setAttribute("class", "gridline");
      svg.appendChild(gl);

      const lbl = document.createElementNS(svgNS, "text");
      lbl.setAttribute("x", M.left - 10);
      lbl.setAttribute("y", y + 3);
      lbl.setAttribute("text-anchor", "end");
      lbl.setAttribute("class", "axis-label");
      lbl.textContent = v.toFixed(decimals);
      svg.appendChild(lbl);
    }}

    const baseline = document.createElementNS(svgNS, "line");
    baseline.setAttribute("x1", M.left); baseline.setAttribute("x2", W - M.right);
    baseline.setAttribute("y1", yFor(0)); baseline.setAttribute("y2", yFor(0));
    baseline.setAttribute("class", "axis-line");
    svg.appendChild(baseline);

    months.forEach((m, i) => {{
      const lbl = document.createElementNS(svgNS, "text");
      lbl.setAttribute("x", xFor(i));
      lbl.setAttribute("y", H - 8);
      lbl.setAttribute("text-anchor", "middle");
      lbl.setAttribute("class", "axis-label");
      lbl.textContent = m;
      svg.appendChild(lbl);
    }});

    providers.forEach(p => {{
      const vals = dataByProvider[p.key];
      const d = vals.map((v, i) => `${{i === 0 ? "M" : "L"}} ${{xFor(i)}} ${{yFor(v)}}`).join(" ");
      const path = document.createElementNS(svgNS, "path");
      path.setAttribute("d", d);
      path.setAttribute("class", "series-line");
      path.setAttribute("stroke", colorOf(p));
      svg.appendChild(path);
    }});

    const crosshair = document.createElementNS(svgNS, "line");
    crosshair.setAttribute("y1", M.top); crosshair.setAttribute("y2", H - M.bottom);
    crosshair.setAttribute("class", "crosshair");
    svg.appendChild(crosshair);

    const dots = providers.map(p => {{
      const dot = document.createElementNS(svgNS, "circle");
      dot.setAttribute("class", "hover-dot");
      dot.setAttribute("fill", colorOf(p));
      dot.style.opacity = 0;
      svg.appendChild(dot);
      return dot;
    }});

    const hit = document.createElementNS(svgNS, "rect");
    hit.setAttribute("x", M.left); hit.setAttribute("y", M.top);
    hit.setAttribute("width", plotW); hit.setAttribute("height", plotH);
    hit.setAttribute("fill", "transparent");
    svg.appendChild(hit);

    container.appendChild(svg);

    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    container.appendChild(tooltip);

    function showAt(i) {{
      const x = xFor(i);
      crosshair.setAttribute("x1", x); crosshair.setAttribute("x2", x);
      crosshair.style.opacity = 1;
      let topY = H;
      dots.forEach((dot, pi) => {{
        const v = dataByProvider[providers[pi].key][i];
        dot.setAttribute("cx", x);
        dot.setAttribute("cy", yFor(v));
        dot.style.opacity = 1;
        topY = Math.min(topY, yFor(v));
      }});

      const rows = providers.map(p => {{
        const v = dataByProvider[p.key][i];
        return `<div class="tt-row"><span style="display:flex;align-items:center;gap:6px;"><span class="tt-dot" style="background:${{colorOf(p)}}"></span>${{p.label}}</span><span class="tt-val">${{v.toFixed(decimals)}} ${{unitLabel}}</span></div>`;
      }}).join("");
      tooltip.innerHTML = `<div class="tt-month">${{months[i]}} {year}</div>${{rows}}`;

      tooltip.style.left = (x / W * 100) + "%";
      tooltip.style.top = (topY / H * 100) + "%";
      tooltip.style.opacity = 1;
    }}
    function hide() {{
      crosshair.style.opacity = 0;
      dots.forEach(d => d.style.opacity = 0);
      tooltip.style.opacity = 0;
    }}

    hit.addEventListener("mousemove", (e) => {{
      const rect = svg.getBoundingClientRect();
      const px = (e.clientX - rect.left) / rect.width * W;
      let i = Math.round((px - M.left) / plotW * (months.length - 1));
      i = Math.max(0, Math.min(months.length - 1, i));
      showAt(i);
    }});
    hit.addEventListener("mouseleave", hide);
    hit.addEventListener("touchstart", (e) => {{
      const rect = svg.getBoundingClientRect();
      const touch = e.touches[0];
      const px = (touch.clientX - rect.left) / rect.width * W;
      let i = Math.round((px - M.left) / plotW * (months.length - 1));
      i = Math.max(0, Math.min(months.length - 1, i));
      showAt(i);
    }}, {{ passive: true }});
  }}

  buildChart("chart-heating", DATA.heatingMonthly, "kWh", 0);
  buildChart("chart-cooling", DATA.coolingMonthly, "kWh", 0);
  buildChart("chart-t", DATA.tMonthly, "\\u00b0C", 1);
  buildChart("chart-ghi", DATA.ghiMonthly, "W/m\\u00b2", 0);

  // ── GHI diff table ──────────────────────────────────────────────
  const diffTable = document.getElementById("ghi-diff-table");
  let dthead = "<thead><tr><th>Pair</th><th>Mean abs. hourly diff (W/m&sup2;)</th><th>Annual total % diff</th></tr></thead>";
  let dtbody = "<tbody>";
  DATA.ghiDiffRows.forEach(r => {{
    dtbody += `<tr><td>${{r.pair}}</td><td>${{r.mean_abs_diff.toLocaleString()}}</td><td>${{r.pct_diff > 0 ? "+" : ""}}${{r.pct_diff.toLocaleString()}}%</td></tr>`;
  }});
  dtbody += "</tbody>";
  diffTable.innerHTML = dthead + dtbody;

  // ── Monthly data table ──────────────────────────────────────────
  const table = document.getElementById("data-table");
  let thead = "<thead><tr><th>Month</th>";
  providers.forEach(p => {{ thead += `<th>${{p.label}}<br>heating kWh</th>`; }});
  providers.forEach(p => {{ thead += `<th>${{p.label}}<br>cooling kWh</th>`; }});
  thead += "</tr></thead>";

  let tbody = "<tbody>";
  months.forEach((m, i) => {{
    tbody += `<tr><td>${{m}}</td>`;
    providers.forEach(p => {{ tbody += `<td style="color:${{colorOf(p)}}">${{DATA.heatingMonthly[p.key][i].toLocaleString()}}</td>`; }});
    providers.forEach(p => {{ tbody += `<td style="color:${{colorOf(p)}}">${{DATA.coolingMonthly[p.key][i].toLocaleString()}}</td>`; }});
    tbody += "</tr>";
  }});
  tbody += "</tbody>";
  table.innerHTML = thead + tbody;
}})();
</script>
</body>
</html>
"""

__all__ = ["build_html_report"]
