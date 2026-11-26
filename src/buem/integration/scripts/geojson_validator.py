"""
Comprehensive JSON schema validation for BUEM GeoJSON payloads.

This module provides robust validation and debugging capabilities for incoming
GeoJSON requests, supporting both legacy and new component structures.
Uses marshmallow for schema validation with detailed error reporting.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd
from marshmallow import Schema, ValidationError, fields, post_load, validate, validates, validates_schema

# occupancy (https://github.com/UU-BUEM/occupancy) is compulsory -- same
# treatment as elsewhere in buem, imported unconditionally like pandas.
from occupancy import SERVICE_BUILDING_TYPES  # type: ignore[import]

# Side-effect-free constants only -- see building_registry.py's own
# docstring for why this doesn't import from cfg_attribute.py directly
# (that module does a real, eager weather fetch at import time; request
# *structure* validation must never require weather archives to be
# configured).
from buem.config.building_registry import (
    DEFAULT_WEATHER_PROVIDER,
    DEFAULT_YEAR,
    RESIDENTIAL_BUILDING_TYPES,
)
from buem.integration.scripts.profile_file_loader import (
    load_electricity_load_values,
    load_weather_profile,
)

# Canonical + alias provider strings accepted by weather.point_query.get_point_weather.
WEATHER_PROVIDERS = ("era5-land", "era5", "cosmo-rea6", "cosmo", "merra-2", "merra2")

# Alias -> canonical provider name (WEATHER_PROVIDERS above accepts both).
_WEATHER_PROVIDER_CANONICAL = {
    "era5-land": "era5-land", "era5": "era5-land",
    "cosmo-rea6": "cosmo-rea6", "cosmo": "cosmo-rea6",
    "merra-2": "merra-2", "merra2": "merra-2",
}

# Real per-provider data-availability windows (calendar year, inclusive) --
# outside these ranges the provider's processed archive doesn't exist or
# isn't meaningful, so a request for one is rejected here (2026-08-14,
# tightened per user consultation) rather than failing opaquely deep in
# weather.get_point_weather()'s own FileNotFoundError. Revisit if the
# archives' actual coverage changes -- these are data-availability facts
# about the provider, not a buem design choice, so any correction should
# come from checking with the `weather` repo, not adjusted freely here.
WEATHER_PROVIDER_YEAR_RANGES = {
    "cosmo-rea6": (1995, 2018),
    "merra-2": (1950, 2025),
    "era5-land": (1980, 2025),
}

# Plausibility bounds for a caller-supplied weather profile, checked at
# the request boundary so a client gets actionable feedback rather than
# an implausible result. Deliberately wide: these flag values that cannot
# be physically correct (or indicate a unit mix-up, e.g. irradiance in
# the wrong unit or temperature in Fahrenheit), not values that are
# merely unusual for a given location. Reported as warnings, never
# errors -- a genuinely extreme but real climate must still be
# simulable. Provider-fetched weather is not checked here; that is the
# `weather` package's own responsibility.
WEATHER_PROFILE_PLAUSIBLE_RANGES = {
    "T": (-60.0, 60.0),      # degC
    "GHI": (0.0, 1500.0),    # W/m2 -- above the solar constant at surface
    "DNI": (0.0, 1500.0),    # W/m2
    "DHI": (0.0, 1500.0),    # W/m2
}

logger = logging.getLogger(__name__)


class SmartComponentsField(fields.Field):
    """Custom field that validates components with proper context for element types."""

    def _serialize(self, value, attr, obj, **kwargs):
        return value

    def _deserialize(self, value, attr, data, **kwargs):
        if not isinstance(value, dict):
            raise ValidationError("Components must be a dictionary")

        errors = {}
        result = {}

        for component_type, component_data in value.items():
            try:
                # Create schema and set context
                schema = ComponentSchema()
                schema.context = {'component_type': component_type}
                result[component_type] = schema.load(component_data)
            except ValidationError as e:
                errors[component_type] = e.messages

        if errors:
            raise ValidationError(errors)

        return result


class ComponentType(str, Enum):
    """Component types for building elements."""
    WALL = "wall"
    ROOF = "roof"
    FLOOR = "floor"
    WINDOW = "window"
    DOOR = "door"
    VENTILATION = "ventilation"


class ValidationLevel(str, Enum):
    """Validation severity levels."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    """Single validation issue with context."""
    level: ValidationLevel
    message: str
    path: str
    value: Any = None
    suggestion: str | None = None


@dataclass
class ValidationResult:
    """Complete validation result with detailed reporting."""
    is_valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    validated_data: dict[str, Any] | None = None

    def add_issue(self, level: ValidationLevel, message: str, path: str,
                  value: Any = None, suggestion: str | None = None):
        """Add a validation issue."""
        self.issues.append(ValidationIssue(level, message, path, value, suggestion))
        if level == ValidationLevel.ERROR:
            self.is_valid = False

    def get_errors(self) -> list[ValidationIssue]:
        """Get only error-level issues."""
        return [issue for issue in self.issues if issue.level == ValidationLevel.ERROR]

    def get_warnings(self) -> list[ValidationIssue]:
        """Get warning-level issues."""
        return [issue for issue in self.issues if issue.level == ValidationLevel.WARNING]

    def summary(self) -> str:
        """Get a summary of validation results."""
        errors = len(self.get_errors())
        warnings = len(self.get_warnings())
        if errors > 0:
            return f"Validation failed: {errors} errors, {warnings} warnings"
        elif warnings > 0:
            return f"Validation passed with {warnings} warnings"
        else:
            return "Validation passed successfully"


class ComponentElementSchema(Schema):
    """Schema for individual building component elements (walls, roof, floor, windows, doors)."""
    id = fields.Str(required=True, validate=lambda x: len(x.strip()) > 0)
    area = fields.Float(required=True, validate=lambda x: x > 0)
    azimuth = fields.Float(required=True, validate=lambda x: 0 <= x <= 360)
    tilt = fields.Float(required=True, validate=lambda x: 0 <= x <= 90)
    # Optional fields for windows/doors
    surface = fields.Str(required=False, allow_none=True)
    U = fields.Float(validate=lambda x: x > 0, required=False, allow_none=True)  # Allow per-element U-values

    @validates('id')
    def validate_id_format(self, value, **kwargs):
        """Validate element ID format."""
        if not value or not value.strip():
            raise ValidationError("Element ID cannot be empty")
        # Add any specific ID format requirements here

    @validates('azimuth')
    def validate_azimuth_range(self, value, **kwargs):
        """Validate azimuth is in valid range."""
        if not 0 <= value <= 360:
            raise ValidationError("Azimuth must be between 0 and 360 degrees")


class VentilationElementSchema(Schema):
    """Schema for ventilation system elements."""
    id = fields.Str(required=True, validate=lambda x: len(x.strip()) > 0)
    air_changes = fields.Float(required=True, validate=lambda x: x >= 0)

    @validates('id')
    def validate_id_format(self, value, **kwargs):
        """Validate element ID format."""
        if not value or not value.strip():
            raise ValidationError("Element ID cannot be empty")

    @validates('air_changes')
    def validate_air_changes_range(self, value, **kwargs):
        """Validate air changes is non-negative."""
        if value < 0:
            raise ValidationError("Air changes must be non-negative")


class ComponentSchema(Schema):
    """Schema for building component (Walls, Roof, etc.)."""
    U = fields.Float(validate=lambda x: x > 0, required=False, allow_none=True)  # Component-level U-value
    g_gl = fields.Float(validate=lambda x: 0 < x < 1, required=False, allow_none=True)  # For windows
    b_transmission = fields.Float(validate=lambda x: x > 0, load_default=1.0)
    elements = fields.Raw(required=True, validate=lambda x: len(x) > 0)

    @validates('elements')
    def validate_elements(self, value, **kwargs):
        """
        Validate elements using appropriate schema based on component type.

        For Ventilation components, use VentilationElementSchema.
        For other components (Walls, Roof, Floor), use ComponentElementSchema.
        """
        if not isinstance(value, list) or len(value) == 0:
            raise ValidationError("Elements must be a non-empty list")

        # Get component type from context
        component_type = self.context.get('component_type')

        # Choose appropriate schema based on component type
        if component_type and 'ventilation' in component_type.lower():
            schema_class = VentilationElementSchema
        else:
            schema_class = ComponentElementSchema

        # Validate each element
        schema = schema_class()
        errors = {}

        for i, element_data in enumerate(value):
            try:
                schema.load(element_data)
            except ValidationError as e:
                errors[i] = e.messages

        if errors:
            raise ValidationError(errors)

    @validates_schema
    def validate_u_value_requirement(self, data, **kwargs):
        """Ensure either component-level or element-level U-values are provided."""
        # Skip U-value validation for ventilation components
        component_type = self.context.get('component_type')
        if component_type and 'ventilation' in component_type.lower():
            return  # Ventilation components use air_changes, not U-values

        component_u = data.get('U')
        elements = data.get('elements', [])

        if component_u is None:
            # Check if all elements have U-values
            for i, element in enumerate(elements):
                if element.get('U') is None:
                    raise ValidationError(
                        f"Element {i} missing U-value when no component-level U is provided",
                        field_name=f'elements.{i}.U'
                    )


class ChildComponentSchema(Schema):
    """Schema for child components (external format)."""
    component_id = fields.Str(required=True)
    component_type = fields.Str(required=True, validate=lambda x: x.lower() in [e.value for e in ComponentType])
    area_m2 = fields.Float(required=True, validate=lambda x: x > 0)
    orientation_deg = fields.Float(required=True, validate=lambda x: 0 <= x <= 360)
    tilt_deg = fields.Float(required=True, validate=lambda x: 0 <= x <= 90)
    u_value = fields.Float(validate=lambda x: x > 0, required=False, allow_none=True)
    surface_reference = fields.Str(required=False, allow_none=True)  # For windows/doors


class BuildingAttributesSchema(Schema):
    """Schema for building attributes."""
    # Location
    latitude = fields.Float(required=True, validate=lambda x: -90 <= x <= 90)
    longitude = fields.Float(required=True, validate=lambda x: -180 <= x <= 180)

    # Weather source: which calendar year and which provider's per-location
    # data to fetch (see buem.config.weather_cache / cfg_attribute.py).
    # Both optional -- year defaults to the request's own start_time, and
    # weather_provider defaults to ATTRIBUTE_SPECS["weather_provider"].
    year = fields.Int(required=False, allow_none=True, validate=lambda x: 1940 <= x <= 2100)
    weather_provider = fields.Str(required=False, allow_none=True, validate=validate.OneOf(WEATHER_PROVIDERS))

    # Basic building properties
    A_ref = fields.Float(validate=lambda x: x > 0, load_default=100.0)
    h_room = fields.Float(validate=lambda x: x > 0, load_default=2.5)

    # Optional external format fields
    country = fields.Str(required=False, allow_none=True)
    building_type = fields.Str(required=False, allow_none=True)
    construction_period = fields.Str(required=False, allow_none=True)
    heated_area_m2 = fields.Float(validate=lambda x: x > 0, required=False, allow_none=True)
    volume_m3 = fields.Float(validate=lambda x: x > 0, required=False, allow_none=True)
    height_m = fields.Float(validate=lambda x: x > 0, required=False, allow_none=True)

    # Components (nested structure - preferred)
    components = SmartComponentsField(required=False, allow_none=True)

    @validates('latitude')
    def validate_latitude(self, value, **kwargs):
        """Validate latitude range."""
        if not -90 <= value <= 90:
            raise ValidationError("Latitude must be between -90 and 90")

    @validates('longitude')
    def validate_longitude(self, value, **kwargs):
        """Validate longitude range."""
        if not -180 <= value <= 180:
            raise ValidationError("Longitude must be between -180 and 180")


class BuemSchema(Schema):
    """Schema for the ``buem`` section of a request feature.

    Only the current (v4) request shape is accepted: a ``building``
    object carrying ``envelope``, plus the optional ``thermal``,
    ``solver``, ``weather`` and ``inputs`` siblings. Their structure is
    validated by the JSON Schema layer, so marshmallow only checks
    presence here.

    ``building_attributes`` is declared but not accepted: it is the
    superseded flat request format, kept in the schema purely so a
    request still sending it fails with a clear migration message from
    :meth:`require_building_envelope` rather than a generic
    "unknown field" error. It remains buem's *internal* representation
    downstream -- ``_convert_v3_to_v2()`` produces it -- but is no longer
    a supported input.
    """
    building_attributes = fields.Nested(BuildingAttributesSchema, required=False, allow_none=True)
    child_components = fields.List(fields.Nested(ChildComponentSchema), required=False, allow_none=True)
    use_milp = fields.Bool(load_default=False)

    building = fields.Dict(required=False, allow_none=True)
    envelope = fields.Dict(required=False, allow_none=True)
    thermal = fields.Dict(required=False, allow_none=True)
    solver = fields.Dict(required=False, allow_none=True)
    weather = fields.Dict(required=False, allow_none=True)
    inputs = fields.Dict(required=False, allow_none=True)

    @validates_schema
    def require_building_envelope(self, data, **kwargs):
        """Require the v4 request shape: ``building`` carrying ``envelope``.

        Superseded request formats are rejected rather than silently
        accepted. In particular a flat ``building_attributes`` payload --
        the pre-v3 shape, which remains buem's *internal* representation
        but is no longer a supported input -- fails here with a message
        naming the field to migrate to.
        """
        has_building_envelope = (
            isinstance(data.get('building'), dict)
            and isinstance(data['building'].get('envelope'), dict)
        )
        if has_building_envelope:
            return
        if data.get('building_attributes') is not None:
            raise ValidationError(
                "'building_attributes' is a superseded request format and is no "
                "longer accepted. Send the current format instead: 'building' "
                "with an 'envelope' object.",
                field_name='buem',
            )
        raise ValidationError(
            "Provide 'building' with an 'envelope' object.",
            field_name='buem',
        )


class PropertiesSchema(Schema):
    """Schema for feature properties."""
    start_time = fields.DateTime(required=True)
    end_time = fields.DateTime(required=True)
    resolution = fields.Raw(load_default="60")
    resolution_unit = fields.Str(load_default="minutes")
    buem = fields.Nested(BuemSchema, required=True)

    @validates('resolution')
    def coerce_resolution(self, value, **kwargs):
        """Accept both int and str, ensure it's a positive number."""
        try:
            int(value)
        except (TypeError, ValueError):
            raise ValidationError("resolution must be a number or numeric string")


class GeometrySchema(Schema):
    """Schema for GeoJSON geometry."""
    type = fields.Str(required=True, validate=lambda x: x == "Point")
    # 2 or 3 elements: [longitude, latitude, elevation?] -- matches
    # request_schema.json's minItems:2/maxItems:3 (elevation optional).
    # Sign-off: 2026-08-12 session, see CLAUDE.md "Guardrails".
    coordinates = fields.List(fields.Float(), required=True, validate=lambda x: len(x) in (2, 3))


class FeatureSchema(Schema):
    """Schema for GeoJSON feature."""
    type = fields.Str(required=True, validate=lambda x: x == "Feature")
    id = fields.Str(required=True)
    geometry = fields.Nested(GeometrySchema, required=True)
    properties = fields.Nested(PropertiesSchema, required=True)


class GeoJsonRequestSchema(Schema):
    """Main schema for GeoJSON request."""
    type = fields.Str(required=True, validate=lambda x: x in ["FeatureCollection", "Feature"])
    features = fields.List(fields.Nested(FeatureSchema), required=True, validate=lambda x: len(x) > 0)
    timeStamp = fields.DateTime(required=False, allow_none=True)
    numberMatched = fields.Int(required=False, allow_none=True)
    numberReturned = fields.Int(required=False, allow_none=True)

    @post_load
    def normalize_single_feature(self, data, **kwargs):
        """Convert single Feature to FeatureCollection if needed."""
        if data.get('type') == 'Feature':
            # Convert single feature to collection
            feature = {k: v for k, v in data.items() if k != 'type'}
            data = {
                'type': 'FeatureCollection',
                'features': [feature]
            }
        return data


class GeoJsonValidator:
    """
    Comprehensive GeoJSON validator with hybrid component support.

    Supports both:
    1. Nested components structure (preferred): components.Walls.elements[]
    2. Flat child_components structure (external): child_components[]

    Provides detailed validation with debugging information.
    """

    def __init__(self, strict_mode: bool = False):
        """
        Initialize validator.

        Parameters
        ----------
        strict_mode : bool
            If True, warnings are treated as errors.
        """
        self.strict_mode = strict_mode
        self.schema = GeoJsonRequestSchema()

    def validate(self, payload: dict[str, Any]) -> ValidationResult:
        """
        Validate GeoJSON payload with comprehensive error reporting.

        Parameters
        ----------
        payload : Dict[str, Any]
            Raw GeoJSON payload to validate.

        Returns
        -------
        ValidationResult
            Detailed validation results with errors, warnings, and suggestions.
        """
        result = ValidationResult(is_valid=True)

        try:
            # Basic schema validation
            validated_data = self.schema.load(payload)
            result.validated_data = validated_data

            # Additional custom validations
            self._validate_features(validated_data['features'], result)
            self._validate_time_consistency(validated_data['features'], result)
            self._convert_components_format(validated_data, result)

        except ValidationError as e:
            result.is_valid = False
            self._process_marshmallow_errors(e.messages, result)
        except (TypeError, ValueError, KeyError, AttributeError) as e:
            result.add_issue(
                ValidationLevel.ERROR,
                f"Unexpected validation error: {e!s}",
                "root",
                suggestion="Check payload format and structure"
            )

        return result

    def _validate_features(self, features: list[dict], result: ValidationResult):
        """Validate individual features."""
        for i, feature in enumerate(features):
            self._validate_single_feature(feature, f"features[{i}]", result)

    def _validate_single_feature(self, feature: dict, path: str, result: ValidationResult):
        """Validate a single feature (supports both v2 and v3 layout)."""
        buem_data = feature.get('properties', {}).get('buem', {})

        # Detect schema version by key presence
        is_v3 = 'building' in buem_data and isinstance(buem_data.get('building'), dict)

        if is_v3:
            # v3: building.envelope.elements carries the component data
            building = buem_data.get('building', {})
            envelope = building.get('envelope', buem_data.get('envelope', {}))
            if not envelope or not envelope.get('elements'):
                result.add_issue(
                    ValidationLevel.ERROR,
                    "No envelope elements found",
                    f"{path}.properties.buem.building.envelope",
                    suggestion="Provide 'elements' list inside 'building.envelope'"
                )

            # building_type validation (v3->v4 promotion, 2026-08-14;
            # sign-off: this session, see CLAUDE.md "Guardrails"). MAJOR/
            # breaking per VERSIONING.md ("validation constraints become
            # stricter") -- an unrecognized building_type now fails here,
            # instead of failing later and less legibly, deep inside
            # AttributeBuilder.generate_electricity_profile() via
            # ServiceBuildingProfile's own ValueError. Sourced dynamically
            # from both live registries (RESIDENTIAL_BUILDING_TYPES and
            # occupancy.SERVICE_BUILDING_TYPES), not a hand-copied enum, so
            # this check can't itself drift the way a static schema enum
            # could (see test_v4_building_type_enum_matches_occupancy for
            # that separate, schema-side drift guard).
            building_type = building.get('building_type')
            if building_type is not None:
                valid_types = RESIDENTIAL_BUILDING_TYPES | set(SERVICE_BUILDING_TYPES)
                if building_type not in valid_types:
                    result.add_issue(
                        ValidationLevel.ERROR,
                        f"Unknown building_type {building_type!r}",
                        f"{path}.properties.buem.building.building_type",
                        suggestion=f"Must be one of: {sorted(valid_types)}",
                    )

            # Reject, rather than silently ignore, schema fields the v3->v4
            # promotion deliberately does NOT wire up yet -- an accepted-
            # but-inert field would otherwise model the wrong building
            # without telling the caller. Both remain real, documented v4
            # draft proposals (versions/v4/DRAFT.md); wiring either is a
            # separate, later task, not attempted in this pass.
            weather_block = buem_data.get('weather')
            if isinstance(weather_block, dict) and weather_block.get('use_percentile'):
                result.add_issue(
                    ValidationLevel.ERROR,
                    "weather.use_percentile is not yet implemented",
                    f"{path}.properties.buem.weather.use_percentile",
                    suggestion="Omit use_percentile/percentile and supply weather.year instead",
                )

            # Per-provider year-range validation (2026-08-14, tightened per
            # user consultation -- see WEATHER_PROVIDER_YEAR_RANGES). A
            # provider is asked for a year its processed archive doesn't
            # cover fails here, with a clear message naming the valid
            # range, instead of an opaque FileNotFoundError three layers
            # deep in weather.get_point_weather() during AttributeBuilder.
            if isinstance(weather_block, dict) and weather_block.get('year') is not None:
                year = weather_block['year']
                # Explicit `str` annotation, not just a plain assignment:
                # weather_block is loosely-typed (Any) at this point, and
                # mypy's dict.get() overload resolution gets ambiguous when
                # an Any-typed default is passed -- it was inferring
                # provider_raw/provider as "str | None" further down even
                # though this value can never actually be None once the
                # explicit-null case below is handled, blocking the
                # _WEATHER_PROVIDER_CANONICAL/WEATHER_PROVIDER_YEAR_RANGES
                # lookups. Pinning the type here (an implicit narrowing
                # cast, not a runtime check) resolves the ambiguity.
                provider_raw: str = weather_block.get('provider', DEFAULT_WEATHER_PROVIDER)
                if provider_raw is None:
                    # dict.get()'s default only applies when the key is
                    # absent, not when it's explicitly null -- an explicit
                    # `"provider": null` must fall back the same way an
                    # omitted key does, not silently skip the year-range
                    # check below.
                    provider_raw = DEFAULT_WEATHER_PROVIDER
                provider = _WEATHER_PROVIDER_CANONICAL.get(provider_raw, provider_raw)
                year_range = WEATHER_PROVIDER_YEAR_RANGES.get(provider)
                if year_range is not None and not (year_range[0] <= year <= year_range[1]):
                    result.add_issue(
                        ValidationLevel.ERROR,
                        f"weather.year {year} is outside {provider}'s available range "
                        f"{year_range[0]}-{year_range[1]}",
                        f"{path}.properties.buem.weather.year",
                        suggestion=(
                            f"Use a year between {year_range[0]} and {year_range[1]} for "
                            f"provider {provider!r}, or omit provider to use the default "
                            f"({DEFAULT_WEATHER_PROVIDER!r})."
                        ),
                    )
            solver_block = buem_data.get('solver')
            if isinstance(solver_block, dict) and solver_block.get('compute_cooling'):
                result.add_issue(
                    ValidationLevel.ERROR,
                    "solver.compute_cooling is not yet implemented",
                    f"{path}.properties.buem.solver.compute_cooling",
                    suggestion=(
                        "Omit compute_cooling -- both heating and cooling loads are "
                        "always computed and returned unconditionally today"
                    ),
                )
        else:
            # v2: building_attributes.components or child_components
            building_attrs = buem_data.get('building_attributes', {})
            child_components = buem_data.get('child_components', [])

            has_nested = 'components' in building_attrs and building_attrs['components']
            has_child = child_components and len(child_components) > 0

            if not has_nested and not has_child:
                result.add_issue(
                    ValidationLevel.ERROR,
                    "No building components found",
                    f"{path}.properties.buem",
                    suggestion="Provide either 'components' in building_attributes or 'child_components'"
                )
            elif has_nested and has_child:
                result.add_issue(
                    ValidationLevel.WARNING,
                    "Both component formats provided, nested 'components' will take precedence",
                    f"{path}.properties.buem",
                    suggestion="Use only one component format for clarity"
                )

    def _validate_time_consistency(self, features: list[dict], result: ValidationResult):
        """Validate time range consistency."""
        for i, feature in enumerate(features):
            props = feature.get('properties', {})
            start_time = props.get('start_time')
            end_time = props.get('end_time')

            if start_time and end_time:
                if isinstance(start_time, str):
                    start_time = datetime.fromisoformat(start_time)
                if isinstance(end_time, str):
                    end_time = datetime.fromisoformat(end_time)

                if start_time >= end_time:
                    result.add_issue(
                        ValidationLevel.ERROR,
                        "end_time must be after start_time",
                        f"features[{i}].properties",
                        suggestion="Check time range validity"
                    )

    def _convert_components_format(self, data: dict, result: ValidationResult):
        """Convert child_components or v3 envelope to nested components format if needed."""
        for i, feature in enumerate(data.get('features', [])):
            buem_data = feature.get('properties', {}).get('buem', {})

            # Check for v3 format: building.envelope.elements
            building = buem_data.get('building')
            if isinstance(building, dict) and isinstance(building.get('envelope'), dict):
                try:
                    self._convert_v3_to_v2(feature, result, i)
                except (TypeError, ValueError, KeyError, AttributeError, IndexError) as e:
                    result.add_issue(
                        ValidationLevel.ERROR,
                        f"Failed to convert v3 format to internal format: {e!s}",
                        f"features[{i}].properties.buem.building"
                    )
                continue

            # v2 child_components conversion
            building_attrs = buem_data.get('building_attributes', {})
            child_components = buem_data.get('child_components', [])

            # If no nested components but have child_components, convert
            if (not building_attrs.get('components') and child_components):
                try:
                    converted = self._child_to_nested_components(child_components)
                    building_attrs['components'] = converted
                    result.add_issue(
                        ValidationLevel.INFO,
                        "Converted child_components to nested components format",
                        f"features[{i}].properties.buem.building_attributes"
                    )
                except (TypeError, ValueError, KeyError, AttributeError) as e:
                    result.add_issue(
                        ValidationLevel.ERROR,
                        f"Failed to convert child_components: {e!s}",
                        f"features[{i}].properties.buem.child_components"
                    )

    def _convert_v3_to_v2(self, feature: dict, result: ValidationResult, feature_idx: int):
        """
        Convert v3 format (building.envelope.elements with {value,unit} objects)
        to v2 format (building_attributes.components) for internal processing.

        The v3 format uses:
          - buem.building.envelope.elements[] with type-based grouping
          - Explicit {value, unit} measurement objects

        This converts to:
          - buem.building_attributes.components.{Walls,Roof,...}.elements[]
          - Plain numeric values
        """
        buem_data = feature['properties']['buem']
        building = buem_data['building']
        envelope = building.get('envelope', {})
        elements = envelope.get('elements', [])
        thermal = building.get('thermal', {})

        # Extract scalar values from {value, unit} objects
        def extract_value(obj):
            """Extract numeric value from either {value, unit} dict or plain value."""
            if isinstance(obj, dict) and 'value' in obj:
                return obj['value']
            return obj

        # Extract building-level attributes
        latitude = feature.get('geometry', {}).get('coordinates', [0, 0])[1]
        longitude = feature.get('geometry', {}).get('coordinates', [0, 0])[0]
        A_ref = extract_value(building.get('A_ref', 100.0))
        h_room = extract_value(building.get('h_room', 2.5))

        # Group elements by type → component categories
        type_map = {
            'wall': 'Walls',
            'roof': 'Roof',
            'floor': 'Floor',
            'window': 'Windows',
            'door': 'Doors',
            'ventilation': 'Ventilation',
        }

        components: dict[str, dict[str, Any]] = {}
        for elem in elements:
            elem_type = elem.get('type', '').lower()
            comp_key = type_map.get(elem_type)
            if not comp_key:
                result.add_issue(
                    ValidationLevel.WARNING,
                    f"Unknown element type '{elem_type}' in envelope",
                    f"features[{feature_idx}].properties.buem.building.envelope",
                    suggestion=f"Valid types: {', '.join(type_map.keys())}"
                )
                continue

            if comp_key not in components:
                components[comp_key] = {'elements': []}

            if elem_type == 'ventilation':
                converted_elem = {
                    'id': elem.get('id', f'Vent_{len(components[comp_key]["elements"])+1}'),
                    'air_changes': extract_value(elem.get('air_changes', 0.5)),
                }
            else:
                converted_elem = {
                    'id': elem.get('id', f'{comp_key}_{len(components[comp_key]["elements"])+1}'),
                    'area': extract_value(elem.get('area', 0)),
                    'azimuth': extract_value(elem.get('azimuth', 0)),
                    'tilt': extract_value(elem.get('tilt', 0)),
                }

                # U-value (per-element)
                if 'U' in elem:
                    converted_elem['U'] = extract_value(elem['U'])

                # b_transmission
                if 'b_transmission' in elem:
                    converted_elem['b_transmission'] = extract_value(elem['b_transmission'])

                # Window-specific: g_gl, parent_id→surface
                if elem_type == 'window':
                    if 'g_gl' in elem:
                        components[comp_key].setdefault('g_gl', extract_value(elem['g_gl']))
                    if 'parent_id' in elem:
                        converted_elem['surface'] = elem['parent_id']

                # Door-specific: parent_id→surface
                if elem_type == 'door' and 'parent_id' in elem:
                    converted_elem['surface'] = elem['parent_id']

            components[comp_key]['elements'].append(converted_elem)

        # Set component-level U-values from first element if all share the same value
        for comp_key, comp_data in components.items():
            if comp_key == 'Ventilation':
                continue
            elems = comp_data['elements']
            u_values = [e.get('U') for e in elems if 'U' in e]
            if u_values and len(u_values) == len(elems) and len(set(u_values)) == 1:
                comp_data['U'] = u_values[0]
                for e in elems:
                    e.pop('U', None)

        # Ensure all required component types exist (validator expects all five)
        _default_U = {'Walls': 1.0, 'Windows': 2.8, 'Roof': 1.0, 'Floor': 1.0, 'Doors': 3.0}
        for required in ('Walls', 'Windows', 'Roof', 'Floor', 'Doors'):
            if required not in components:
                components[required] = {'U': _default_U[required], 'elements': []}

        # Extract thermal parameters
        n_air_infiltration = extract_value(thermal.get('n_air_infiltration', 0.5))
        n_air_use = extract_value(thermal.get('n_air_use', 0.5))
        comfortT_lb = extract_value(thermal.get('comfortT_lb', 21))
        comfortT_ub = extract_value(thermal.get('comfortT_ub', 24))
        c_m = extract_value(thermal.get('c_m', 165.0))
        design_T_min = extract_value(thermal.get('design_T_min', -12.0))
        F_sh_hor = extract_value(thermal.get('F_sh_hor', 0.8))
        F_sh_vert = extract_value(thermal.get('F_sh_vert', 0.75))
        F_f = extract_value(thermal.get('F_f', 0.2))
        F_w = extract_value(thermal.get('F_w', 1.0))

        # Build v2 building_attributes
        building_attributes = {
            'latitude': latitude,
            'longitude': longitude,
            'A_ref': A_ref,
            'h_room': h_room,
            'components': components,
            # Thermal parameters
            'n_air_infiltration': n_air_infiltration,
            'n_air_use': n_air_use,
            'comfortT_lb': comfortT_lb,
            'comfortT_ub': comfortT_ub,
            'c_m': c_m,
            'design_T_min': design_T_min,
            'F_sh_hor': F_sh_hor,
            'F_sh_vert': F_sh_vert,
            'F_f': F_f,
            'F_w': F_w,
        }

        # Optional thermal parameters (pass through when present)
        for thermal_key in ('phi_int', 'q_w_nd', 'F_red_htr'):
            if thermal_key in thermal:
                building_attributes[thermal_key] = extract_value(thermal[thermal_key])

        # thermalClass (v3 uses 'thermal_class')
        tc = thermal.get('thermal_class')
        if tc:
            building_attributes['thermalClass'] = tc

        # g_gl_n_Window: extract from Windows component g_gl if set
        win_comp = components.get('Windows', {})
        if 'g_gl' in win_comp:
            building_attributes['g_gl_n_Window'] = win_comp['g_gl']

        # Optional building metadata. capacity/num_persons/archetype/
        # residential_units forward occupancy generation inputs --
        # AttributeBuilder.generate_electricity_profile() reads them from
        # merged_attrs and does its own numeric casts, so a plain
        # passthrough is sufficient here. seed is deliberately NOT
        # forwarded: it is an internal reproducibility knob, not part of
        # the EnerPlanET request contract. The EV, PV and home-battery fields
        # (v4 draft, 2026-11-24) are checked by AttributeBuilder
        # (validator.validate_installations, occupancy's EV config).
        for key in ('building_type', 'construction_period', 'country', 'n_storeys',
                    'neighbour_status', 'attic_condition', 'cellar_condition',
                    'capacity', 'num_persons', 'residential_units', 'archetype',
                    'equipment', 'window_to_wall_ratio',
                    'has_ev', 'ev_ownership_probability', 'ev_charger_kw',
                    'has_pv', 'pv_capacity_kwp',
                    'has_battery', 'battery_capacity_kwh'):
            if key in building:
                building_attributes[key] = building[key]

        # Weather source selection (buem.weather.provider/year, v3->v4
        # promotion 2026-08-14; sign-off: this session, see CLAUDE.md
        # "Guardrails"). Previously only reachable via a genuine v2-format
        # request or a direct AttributeBuilder call -- see CLAUDE.md
        # "Weather is compulsory"'s "Gap found" note. use_percentile/
        # percentile remain deliberately unforwarded (rejected above, not
        # silently ignored): they need weather's own percentile-year
        # infrastructure wired through the point-query HTTP API first, an
        # unstarted, separate piece of work (see versions/v4/DRAFT.md).
        #
        # Deliberately does NOT fall back to start_time's calendar year
        # despite DRAFT.md's documented intent for the year field:
        # confirmed against real fixtures that this can silently request a
        # year for which no local archive exists (start_time is often "now"
        # in a request, not the archived weather year), turning a working
        # request into a hard failure. year is forwarded only when the
        # caller explicitly supplies buem.weather.year -- omitting it keeps
        # today's AttributeBuilder-default-year behavior unchanged, exactly
        # as before this promotion.
        weather_block = buem_data.get('weather') or {}
        resolved_year = None
        if isinstance(weather_block, dict) and weather_block.get('year') is not None:
            resolved_year = int(weather_block['year'])
            building_attributes['year'] = resolved_year
        if isinstance(weather_block, dict) and weather_block.get('provider'):
            building_attributes['weather_provider'] = weather_block['provider']

        # Caller-supplied weather profile file (buem.weather.profile) --
        # overrides the provider/year fetch entirely when present.
        weather_profile = weather_block.get('profile') if isinstance(weather_block, dict) else None
        if isinstance(weather_profile, dict) and weather_profile.get('path'):
            weather_df = load_weather_profile(
                weather_profile['path'], weather_profile.get('format', 'json'),
            )
            self._check_weather_profile_ranges(weather_df, result, feature_idx)
            building_attributes['weather'] = weather_df
            building_attributes['use_provided_weather'] = True

        # Caller-supplied electricity load profile file
        # (buem.inputs.electricity_load_profile) -- a flat hourly array
        # with no timestamp column, indexed here against the resolved
        # year above (or AttributeBuilder's own DEFAULT_YEAR if none was
        # resolved). A length that doesn't match the real weather index is
        # not checked here -- AttributeBuilder._reindex_or_raise() catches
        # a genuine mismatch downstream with a clear error, once the real
        # weather index is known.
        inputs_block = buem_data.get('inputs')
        elec_profile = inputs_block.get('electricity_load_profile') if isinstance(inputs_block, dict) else None
        if isinstance(elec_profile, dict) and elec_profile.get('path'):
            values = load_electricity_load_values(
                elec_profile['path'], elec_profile.get('unit', 'kWh'),
            )
            index = pd.date_range(
                f"{resolved_year or DEFAULT_YEAR}-01-01", periods=len(values), freq="h",
            )
            building_attributes['elecLoad'] = pd.Series(values, index=index, name='elecLoad')
            building_attributes['use_provided_elecLoad'] = True

        # Extract solver settings
        solver = buem_data.get('solver', {})
        use_milp = solver.get('use_milp', False)

        # Replace buem section with v2 format
        buem_data['building_attributes'] = building_attributes
        buem_data['use_milp'] = use_milp

        result.add_issue(
            ValidationLevel.INFO,
            "Converted v3 format (building.envelope) to v2 internal format (building_attributes.components)",
            f"features[{feature_idx}].properties.buem"
        )

    @staticmethod
    def _check_weather_profile_ranges(
        weather_df: pd.DataFrame, result: ValidationResult, feature_idx: int,
    ):
        """Range-check a caller-supplied weather profile.

        Runs at the request boundary, where a client can still correct
        the input, rather than inside the thermal model -- which consumes
        weather exactly as given and never masks or adjusts it. Issues
        are warnings, not errors: a real but extreme climate must remain
        simulable, so this surfaces likely unit mix-ups and corrupt data
        without blocking the request. See
        ``WEATHER_PROFILE_PLAUSIBLE_RANGES``.
        """
        path = f"features[{feature_idx}].properties.buem.weather.profile"
        for column, (low, high) in WEATHER_PROFILE_PLAUSIBLE_RANGES.items():
            if column not in weather_df.columns:
                continue
            series = weather_df[column]
            observed_min, observed_max = float(series.min()), float(series.max())
            if observed_min < low or observed_max > high:
                result.add_issue(
                    ValidationLevel.WARNING,
                    f"weather.profile column {column!r} spans "
                    f"{observed_min:.1f} to {observed_max:.1f}, outside the "
                    f"plausible range {low} to {high}",
                    path,
                    suggestion=(
                        f"Check {column!r}'s units and source data. "
                        "The profile is used exactly as supplied -- buem does "
                        "not clip or adjust weather values."
                    ),
                )

    def _child_to_nested_components(self, child_components: list[dict]) -> dict[str, Any]:
        """Convert child_components array to nested components structure."""
        components: dict[str, dict[str, Any]] = {}

        # Group by component type
        for child in child_components:
            comp_type = child['component_type'].lower()

            # Map component types
            if comp_type == 'wall':
                comp_key = 'Walls'
            elif comp_type == 'roof':
                comp_key = 'Roof'
            elif comp_type == 'floor':
                comp_key = 'Floor'
            elif comp_type == 'window':
                comp_key = 'Windows'
            elif comp_type == 'door':
                comp_key = 'Doors'
            else:
                comp_key = comp_type.title()

            if comp_key not in components:
                components[comp_key] = {'elements': []}

            # Convert to element format
            element = {
                'id': child['component_id'],
                'area': child['area_m2'],
                'azimuth': child['orientation_deg'],
                'tilt': child['tilt_deg']
            }

            if child.get('u_value'):
                element['U'] = child['u_value']
            if child.get('surface_reference'):
                element['surface'] = child['surface_reference']

            components[comp_key]['elements'].append(element)

        # Set default U-values if not provided per-element
        default_u_values = {
            'Walls': 1.6,
            'Roof': 1.5,
            'Floor': 1.7,
            'Windows': 2.5,
            'Doors': 3.5
        }

        for comp_key, comp_data in components.items():
            elements = comp_data['elements']
            has_element_u = any(elem.get('U') for elem in elements)

            if not has_element_u and comp_key in default_u_values:
                comp_data['U'] = default_u_values[comp_key]

            # Add special properties for windows
            if comp_key == 'Windows':
                comp_data['g_gl'] = 0.5  # Default solar gain

        return components

    def _process_marshmallow_errors(self, errors: dict | list | str, result: ValidationResult):
        """Process marshmallow validation errors."""
        self._flatten_errors(errors, result, "")

    def _flatten_errors(self, errors: dict | list | str, result: ValidationResult, path: str):
        """Recursively flatten nested error messages with actionable suggestions."""
        if isinstance(errors, dict):
            for key, value in errors.items():
                new_path = f"{path}.{key}" if path else key
                self._flatten_errors(value, result, new_path)
        elif isinstance(errors, list):
            for error in errors:
                result.add_issue(
                    ValidationLevel.ERROR,
                    str(error),
                    path,
                    suggestion=self._suggest_fix(str(error), path)
                )
        else:
            result.add_issue(
                ValidationLevel.ERROR,
                str(errors),
                path,
                suggestion=self._suggest_fix(str(errors), path)
            )

    @staticmethod
    def _suggest_fix(error_msg: str, path: str) -> str:
        """Return an actionable suggestion based on the error text and field path."""
        msg = error_msg.lower()

        if "missing data for required field" in msg:
            field_name = path.rsplit('.', 1)[-1] if '.' in path else path
            if field_name == 'building_attributes':
                return ("v2 format expects 'building_attributes' inside buem. "
                        "If using v3 format, provide 'building' and 'envelope' instead")
            return f"Add the required '{field_name}' field"

        if "unknown field" in msg:
            field_name = path.rsplit('.', 1)[-1] if '.' in path else path
            if field_name in ('building', 'envelope', 'thermal', 'solver'):
                return ("These are v3 schema keys. Ensure the marshmallow domain "
                        "validator and JSON Schema version both target v3")
            return f"'{field_name}' is not recognised at this level — check spelling or nesting"

        if "not a valid" in msg:
            return f"Value at '{path}' has the wrong type — check the expected format"

        if "must be" in msg or "between" in msg:
            return f"Value at '{path}' is out of range — see the constraint in the error"

        if "length" in msg or "empty" in msg:
            return f"'{path}' must not be empty"

        return f"Review the value at '{path}' — see error message above for details"


def validate_geojson_request(payload: dict[str, Any], strict_mode: bool = False) -> ValidationResult:
    """
    Convenience function to validate GeoJSON request.

    Parameters
    ----------
    payload : Dict[str, Any]
        GeoJSON payload to validate.
    strict_mode : bool
        Treat warnings as errors.

    Returns
    -------
    ValidationResult
        Validation results with detailed error reporting.
    """
    validator = GeoJsonValidator(strict_mode=strict_mode)
    return validator.validate(payload)


def create_validation_report(result: ValidationResult) -> str:
    """
    Create a detailed validation report.

    Parameters
    ----------
    result : ValidationResult
        Validation result to report.

    Returns
    -------
    str
        Formatted validation report.
    """
    report = ["=== VALIDATION REPORT ==="]
    report.append(f"Status: {result.summary()}")
    report.append("")

    if result.get_errors():
        report.append("ERRORS:")
        for issue in result.get_errors():
            report.append(f"  ❌ {issue.path}: {issue.message}")
            if issue.suggestion:
                report.append(f"     💡 Suggestion: {issue.suggestion}")
        report.append("")

    if result.get_warnings():
        report.append("WARNINGS:")
        for issue in result.get_warnings():
            report.append(f"  ⚠️  {issue.path}: {issue.message}")
            if issue.suggestion:
                report.append(f"     💡 Suggestion: {issue.suggestion}")
        report.append("")

    info_issues = [i for i in result.issues if i.level == ValidationLevel.INFO]
    if info_issues:
        report.append("INFO:")
        for issue in info_issues:
            report.append(f"  ℹ️  {issue.path}: {issue.message}")
        report.append("")

    return "\n".join(report)
