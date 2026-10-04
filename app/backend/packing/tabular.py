"""Compact, token-cheap serialization of records for LLM prompts.

Instead of repeating every key per record (JSON / Python repr), records are
flattened and emitted as a table: one header row, then one row per record.

    >>> box = {"name": "Small", "dimensions": {"x": 12, "y": 6, "z": 6.75}}
    >>> print(to_table("boxes", [box]))
    boxes (1 rows)
    name|dimensions.x|dimensions.y|dimensions.z
    Small|12|6|6.75
"""

import math
from collections.abc import Iterable, Mapping
from typing import Any


def flatten(
    record: Mapping[str, Any], sep: str = ".", prefix: str = ""
) -> dict[str, Any]:
    """Flatten nested mappings into one level using dotted keys."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        full_key = f"{prefix}{sep}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flat.update(flatten(value, sep, full_key))
        else:
            flat[full_key] = value
    return flat


def _format_cell(value: Any, delimiter: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # 4.0 -> "4", 3.250 -> "3.25"
        return "" if math.isnan(value) else f"{value:g}"
    if isinstance(value, (list, tuple)):
        return ";".join(_format_cell(v, delimiter) for v in value)
    # Keep rows intact if a string contains the delimiter or newlines.
    return str(value).replace(delimiter, "/").replace("\n", " ")


def to_table(
    name: str,
    records: Iterable[Mapping[str, Any]],
    *,
    columns: list[str] | None = None,
    rename: Mapping[str, str] | None = None,
    delimiter: str = "|",
) -> str:
    """Render records as a titled, delimiter-separated table.

    columns: flattened keys to include, in order
             (default: all keys, first-seen order).
    rename:  flattened key -> header label, e.g. {"dimensions.x": "x"}.
    """
    flat_records = []
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError(
                f"{name}: expected a mapping per record, "
                f"got {type(record).__name__}: {record!r}"
            )
        flat_records.append(flatten(record))

    if columns is None:
        columns = list(
            dict.fromkeys(key for record in flat_records for key in record)
        )

    rename = rename or {}
    header = delimiter.join(rename.get(col, col) for col in columns)
    rows = [
        delimiter.join(
            _format_cell(record.get(col), delimiter) for col in columns
        )
        for record in flat_records
    ]
    return "\n".join([f"{name} ({len(rows)} rows)", header, *rows])
