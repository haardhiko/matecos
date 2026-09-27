"""
Builtin tool: Unit Convert.

Tool ID: ``data.convert.units``
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="data.convert.units", execution_id=context.execution_id, agent_id=context.agent_id)
    value = float(payload.get("value", 0))
    raw_from = payload.get("from_unit", "").strip().lower()
    raw_to = payload.get("to_unit", "").strip().lower()

    unit_aliases = {
        "c": "c", "celsius": "c", "centigrade": "c",
        "f": "f", "fahrenheit": "f",
        "k": "k", "kelvin": "k",
        "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
        "km": "km", "kilometer": "km", "kilometers": "km",
        "mi": "mi", "mile": "mi", "miles": "mi",
        "ft": "ft", "foot": "ft", "feet": "ft",
        "in": "in", "inch": "in", "inches": "in",
        "cm": "cm", "centimeter": "cm", "centimeters": "cm",
        "mm": "mm", "millimeter": "mm", "millimeters": "mm",
        "yd": "yd", "yard": "yd", "yards": "yd",
        "kg": "kg", "kilogram": "kg", "kilograms": "kg", "kgs": "kg",
        "g": "g", "gram": "g", "grams": "g",
        "mg": "mg", "milligram": "mg", "milligrams": "mg",
        "lb": "lb", "pound": "lb", "pounds": "lb", "lbs": "lb",
        "oz": "oz", "ounce": "oz", "ounces": "oz",
        "ton": "ton", "tons": "ton", "tonne": "ton", "tonnes": "ton",
        "b": "b", "byte": "b", "bytes": "b",
        "kb": "kb", "kilobyte": "kb", "kilobytes": "kb",
        "mb": "mb", "megabyte": "mb", "megabytes": "mb",
        "gb": "gb", "gigabyte": "gb", "gigabytes": "gb",
        "tb": "tb", "terabyte": "tb", "terabytes": "tb",
        "s": "s", "sec": "s", "second": "s", "seconds": "s",
        "min": "min", "minute": "min", "minutes": "min",
        "h": "h", "hr": "h", "hour": "h", "hours": "h",
        "day": "day", "days": "day",
        "week": "week", "weeks": "week",
        "month": "month", "months": "month",
        "year": "year", "years": "year",
    }
    from_unit = unit_aliases.get(raw_from, raw_from)
    to_unit = unit_aliases.get(raw_to, raw_to)
    
    result = 0.0
    formula = f"{value} {raw_from} -> {raw_to}"
    
    # temp
    if from_unit in ['c', 'f', 'k'] and to_unit in ['c', 'f', 'k']:
        c = 0.0
        if from_unit == 'c': c = value
        elif from_unit == 'f': c = (value - 32) * 5/9
        elif from_unit == 'k': c = value - 273.15
        
        if to_unit == 'c': result = c
        elif to_unit == 'f': result = (c * 9/5) + 32
        elif to_unit == 'k': result = c + 273.15
    # length (base m)
    elif from_unit in ['m', 'km', 'mi', 'ft', 'in', 'cm', 'mm', 'yd'] and to_unit in ['m', 'km', 'mi', 'ft', 'in', 'cm', 'mm', 'yd']:
        factors = {'m': 1, 'km': 1000, 'mi': 1609.34, 'ft': 0.3048, 'in': 0.0254, 'cm': 0.01, 'mm': 0.001, 'yd': 0.9144}
        m = value * factors[from_unit]
        result = m / factors[to_unit]
    # weight (base kg)
    elif from_unit in ['kg', 'lb', 'oz', 'g', 'mg', 'ton'] and to_unit in ['kg', 'lb', 'oz', 'g', 'mg', 'ton']:
        factors = {'kg': 1, 'lb': 0.453592, 'oz': 0.0283495, 'g': 0.001, 'mg': 0.000001, 'ton': 1000}
        kg = value * factors[from_unit]
        result = kg / factors[to_unit]
    # data (base b)
    elif from_unit in ['b', 'kb', 'mb', 'gb', 'tb'] and to_unit in ['b', 'kb', 'mb', 'gb', 'tb']:
        factors = {'b': 1, 'kb': 1024, 'mb': 1024**2, 'gb': 1024**3, 'tb': 1024**4}
        b = value * factors[from_unit]
        result = b / factors[to_unit]
    # time (base s)
    elif from_unit in ['s', 'min', 'h', 'day', 'week', 'month', 'year'] and to_unit in ['s', 'min', 'h', 'day', 'week', 'month', 'year']:
        factors = {'s': 1, 'min': 60, 'h': 3600, 'day': 86400, 'week': 604800, 'month': 2629800, 'year': 31557600}
        s = value * factors[from_unit]
        result = s / factors[to_unit]
    else:
        raise ValueError(f"Incompatible or unknown units: {raw_from} ({from_unit}) to {raw_to} ({to_unit})")
        
    return {
        "result": result,
        "from_unit": from_unit,
        "to_unit": to_unit,
        "formula": formula
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="data.convert.units",
        name="Unit Converter",
        version="1.0.0",
        description="Converts units.",
        capabilities=["convert", "units", "measurement", "temperature", "length", "weight"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["value", "from_unit", "to_unit"],
            "additionalProperties": False,
            "properties": {
                "value": {"type": "number"},
                "from_unit": {"type": "string"},
                "to_unit": {"type": "string"},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["result", "from_unit", "to_unit", "formula"],
            "properties": {
                "result": {"type": "number"},
                "from_unit": {"type": "string"},
                "to_unit": {"type": "string"},
                "formula": {"type": "string"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

UNIT_CONVERT_MANIFEST = get_manifest()
