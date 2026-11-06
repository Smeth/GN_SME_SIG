"""Keep text literal in spreadsheet CSVs, with optional lossless decoding."""
from __future__ import annotations

CSV_TEXT_ENCODING = "spreadsheet-safe-v1"


def spreadsheet_cell(value):
    if isinstance(value, str) and (
        value.startswith(("'", "\t", "\r", "\n"))
        or value.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value


def decode_spreadsheet_cell(value):
    return value[1:] if isinstance(value, str) and value.startswith("'") else value
