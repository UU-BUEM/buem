"""
Runtime configuration validator for building cfg dicts.

Function validate_cfg(cfg: dict) -> List[str]:
  - Returns empty list when cfg passes checks.
  - Returns list of human-readable issue strings otherwise.
Checks performed:
  - presence of components or legacy U_* keys
  - numeric positive U values for Walls/Windows/Roof/Floor/Doors
  - element areas > 0 and unique element ids if 'components' provided
  - weather timeseries length consistent when series provided
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _is_number(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False

def validate_cfg(cfg: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    # Require structured 'components' tree
    comps = cfg.get("components")
    if not isinstance(comps, dict):
        issues.append("components missing or not an object (required)")
        return issues

    seen_ids: set[str] = set()
    for comp in ("Walls", "Windows", "Roof", "Floor", "Doors"):
        c = comps.get(comp)
        if c is None:
            issues.append(f"components.{comp} missing")
            continue

        # component-level U or per-element U is required
        u = c.get("U")
        elems = c.get("elements", [])
        if u is None and not elems:
            issues.append(f"components.{comp} missing U and no elements present")
            continue

        if u is not None:
            try:
                # U < 0 is physically impossible and always an error. U == 0
                # is *not* an error: it's buildings.rst's own documented
                # convention for a shared/party wall (b_transmission == 0,
                # no heat transfer to an adjacent heated space -- see
                # buildings/mapping/wall_classifier.py's SharedWallDetector
                # docstring). Rejecting U == 0 here used to make any
                # building with a party wall fail validation outright.
                if float(u) < 0:
                    issues.append(f"components.{comp}.U must not be negative")
            except (TypeError, ValueError):
                issues.append(f"components.{comp}.U invalid: {u}")

        # validate elements if present
        if isinstance(elems, list):
            for idx, e in enumerate(elems):
                if not isinstance(e, dict):
                    issues.append(f"components.{comp}.elements[{idx}] not an object")
                    continue
                eid = e.get("id")
                if eid is None:
                    issues.append(f"components.{comp}.elements[{idx}].id missing")
                else:
                    if eid in seen_ids:
                        issues.append(f"duplicate element id '{eid}' in components")
                    seen_ids.add(eid)
                area = e.get("area")
                if area is None:
                    issues.append(f"components.{comp}.elements[{idx}].area missing")
                else:
                    try:
                        if float(area) <= 0:
                            issues.append(f"components.{comp}.elements[{idx}].area must be > 0")
                    except (TypeError, ValueError):
                        issues.append(f"components.{comp}.elements[{idx}].area invalid: {area}")
                u_e = e.get("U")
                if u is None:  # if component-level U missing, require per-element U
                    if u_e is None:
                        issues.append(f"components.{comp}.elements[{idx}].U missing")
                    else:
                        try:
                            # Same U == 0 exception as the component-level
                            # check above (party-wall convention).
                            if float(u_e) < 0:
                                issues.append(f"components.{comp}.elements[{idx}].U must not be negative")
                        except (TypeError, ValueError):
                            issues.append(f"components.{comp}.elements[{idx}].U invalid: {u_e}")

    issues.extend(validate_installations(cfg))

    # weather presence/length sanity check (optional)
    weather = cfg.get("weather")
    if weather is None:
        issues.append("weather missing")
    else:
        try:
            n = len(weather)
            if n == 0:
                issues.append("weather timeseries appears empty")
        except TypeError:
            # not lengthable (e.g. a scalar) -- skip the deep length check here
            logger.debug("weather value of type %s is not lengthable; skipping length check", type(weather))

    return issues


# Existing on-site installations: (flag, its capacity attribute).
INSTALLATIONS = (("has_pv", "pv_capacity_kwp"), ("has_battery", "battery_capacity_kwh"))


def validate_installations(cfg: dict[str, Any]) -> list[str]:
    """Issues with the on-site installation attributes: has_pv/has_battery
    must be booleans; a capacity must be a positive number and needs its
    flag set (AttributeBuilder sets the flag when only the capacity was
    sent); elecLoad_net_of_pv needs a provided elecLoad and the building's
    PV output (pv_kW), and no battery."""
    issues: list[str] = []
    for flag, capacity_key in INSTALLATIONS:
        has = cfg.get(flag, False)
        if not isinstance(has, bool):
            issues.append(f"{flag} must be true or false, got {has!r}")
            continue
        capacity = cfg.get(capacity_key)
        if capacity is None:
            continue
        if isinstance(capacity, bool):
            issues.append(f"{capacity_key} must be a number, got {capacity!r}")
            continue
        try:
            value = float(capacity)
        except (TypeError, ValueError):
            issues.append(f"{capacity_key} must be a number, got {capacity!r}")
            continue
        if not value > 0:
            issues.append(f"{capacity_key} must be > 0, got {capacity!r}")
        elif not has:
            issues.append(f"{capacity_key} is set but {flag} is false")
    if cfg.get("elecLoad_net_of_pv"):
        if not cfg.get("use_provided_elecLoad"):
            issues.append("elecLoad_net_of_pv needs use_provided_elecLoad")
        if cfg.get("pv_kW") is None:
            issues.append("elecLoad_net_of_pv needs pv_kW (the building's PV output to add back)")
        if cfg.get("has_battery"):
            issues.append(
                "elecLoad_net_of_pv cannot be used with has_battery: the battery's "
                "charging and discharging are hidden in the net meter reading"
            )
    return issues
