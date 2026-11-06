"""Synthetic table benchmark; no MySQL, Access or user files are opened."""
import argparse
from copy import copy
from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from openpyxl import Workbook
from sme_gend.services.export_workbook_tables import table_sheet


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=project /
                        "build/corrections_20261029/baseline/src/sme_gend/services/export_workbook_tables.py")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    spec = spec_from_file_location("benchmark_baseline_export", args.baseline)
    baseline = module_from_spec(spec)
    spec.loader.exec_module(baseline)
    headers = ["N°", "Grade", "Nom", "Structure", "Position", "Date début", "Date fin", "Observation"]
    rows = [[i, "LTN", f"SYNTHETIC {i}", "UNIT TEST", "CongéAdmin", date(2026, 9, 1),
             date(2026, 9, 30), "Synthetic observation"] for i in range(5700)]

    def measure(writer):
        elapsed = []
        for _ in range(args.runs):
            wb = Workbook()
            started = perf_counter()
            writer(wb, "Table", "Synthetic benchmark", headers, rows, mois="2026-09")
            wb.save(BytesIO())
            elapsed.append(round(perf_counter() - started, 3))
            wb.close()
        return wb, {"times": elapsed, "median": median(elapsed)}

    old, before = measure(baseline.table_sheet)
    new, after = measure(table_sheet)
    a, b = old["Table"], new["Table"]
    assert (a.max_row, a.max_column) == (b.max_row, b.max_column)
    for row in a:
        for cell in row:
            other = b[cell.coordinate]
            assert cell.value == other.value
            for name in ("font", "border", "alignment", "fill", "number_format"):
                assert copy(getattr(cell, name)) == copy(getattr(other, name))
    assert a.auto_filter.ref == b.auto_filter.ref and a.freeze_panes == b.freeze_panes
    assert {i: r.height for i, r in a.row_dimensions.items()} == {i: r.height for i, r in b.row_dimensions.items()}
    print(json.dumps({"rows": 5700, "columns": 8, "runs": args.runs, "before": before,
                      "after": after, "speedup": round(before["median"] / after["median"], 2),
                      "values_and_styles_equal": True}))


if __name__ == "__main__":
    main()
