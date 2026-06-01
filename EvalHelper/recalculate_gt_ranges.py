from __future__ import annotations

import argparse
import csv
import math
import tempfile
from pathlib import Path


LUMEN_PER_LUX = 2 * math.pi * (3.0**2) * (1.0 - math.cos(math.radians(30.0)))


def parse_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def parse_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def parse_bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def time_of_day_target(hour: float, occupancy: int, movement: bool) -> tuple[float, float]:
    if occupancy < 1:
        return 0.0, 0.0

    if 23.0 <= hour or hour < 6.0:
        return (50.0, 50.0) if movement else (0.0, 0.0)
    if 6.0 <= hour < 9.0:
        return 100.0, 200.0
    if 9.0 <= hour < 14.0:
        return 300.0, 500.0
    if 14.0 <= hour < 18.0:
        return 200.0, 300.0
    if 18.0 <= hour < 23.0:
        return 200.0, 300.0
    return 0.0, 0.0


def lux_to_lumen_range(target_low_lux: float, target_high_lux: float, ambient_lux: float) -> tuple[int, int]:
    lamp_low_lux = max(0.0, target_low_lux - ambient_lux)
    lamp_high_lux = max(0.0, target_high_lux - ambient_lux)

    if math.isclose(target_low_lux, target_high_lux, rel_tol=0.0, abs_tol=1e-12):
        lumen = int(round(lamp_low_lux * LUMEN_PER_LUX))
        return max(0, lumen), max(0, lumen)

    low_lumen = int(math.ceil(lamp_low_lux * LUMEN_PER_LUX - 1e-9))
    high_lumen = int(math.floor(lamp_high_lux * LUMEN_PER_LUX + 1e-9))

    low_lumen = max(0, low_lumen)
    high_lumen = max(low_lumen, high_lumen)
    return low_lumen, high_lumen


def compute_gt_range(row: dict[str, str]) -> tuple[int, int]:
    ambient_lux = parse_float(row.get("ambient_light_lux", 0.0))
    occupancy = parse_int(row.get("occupancy_count", 0))
    movement = parse_bool(row.get("motion_detected", False))
    hour = parse_float(row.get("hour", 0.0))

    target_low_lux, target_high_lux = time_of_day_target(hour, occupancy, movement)
    return lux_to_lumen_range(target_low_lux, target_high_lux, ambient_lux)


def format_range(low: int, high: int) -> str:
    return f"{low}-{high}"


def recalculate_csv(input_path: Path, output_path: Path) -> tuple[int, int]:
    changed = 0
    total = 0

    with input_path.open("r", encoding="utf-8", newline="") as src, output_path.open(
        "w", encoding="utf-8", newline=""
    ) as dst:
        reader = csv.DictReader(src)
        if reader.fieldnames is None:
            raise ValueError("Input CSV has no header")

        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        writer.writeheader()

        for row in reader:
            total += 1
            expected_low, expected_high = compute_gt_range(row)
            expected = format_range(expected_low, expected_high)
            current = (row.get("GT - Target") or "").strip()
            if current != expected:
                changed += 1
            row["GT - Target"] = expected
            writer.writerow(row)

    return changed, total


def validate_csv(input_path: Path) -> list[tuple[str, str, str]]:
    mismatches: list[tuple[str, str, str]] = []
    with input_path.open("r", encoding="utf-8", newline="") as src:
        reader = csv.DictReader(src)
        for row in reader:
            timestamp = (row.get("timestamp") or "").strip()
            expected_low, expected_high = compute_gt_range(row)
            expected = format_range(expected_low, expected_high)
            actual = (row.get("GT - Target") or "").strip()
            if actual != expected:
                mismatches.append((timestamp, actual, expected))
    return mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recalculate GT lumen ranges from ambient lux and time-of-day rules"
    )
    parser.add_argument("input", type=Path, help="Input CSV file")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="Optional output CSV path. If omitted, only validation is performed.",
    )
    args = parser.parse_args()

    if args.output is None:
        mismatches = validate_csv(args.input)
        print(f"Validated {args.input}")
        print(f"Mismatches: {len(mismatches)}")
        for timestamp, actual, expected in mismatches[:25]:
            print(f"- {timestamp}: actual={actual} expected={expected}")
        return

    if args.output == args.input:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            encoding="utf-8",
            newline="",
            dir=str(args.input.parent),
        ) as tmp:
            temp_path = Path(tmp.name)
        changed, total = recalculate_csv(args.input, temp_path)
        temp_path.replace(args.input)
        print(f"Updated {args.input} in place")
        print(f"Rows processed: {total}")
        print(f"Rows changed: {changed}")
        return

    changed, total = recalculate_csv(args.input, args.output)
    print(f"Wrote {args.output}")
    print(f"Rows processed: {total}")
    print(f"Rows changed: {changed}")


if __name__ == "__main__":
    main()