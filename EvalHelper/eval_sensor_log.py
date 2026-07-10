from __future__ import annotations

import argparse
import ast
import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any


LOOP_DATASET_SECONDS = 7 * 24 * 60 * 60
DATASET_SPEED_FACTOR = 600.0
SIMULATOR_STARTED_AT = datetime(1997, 6, 20, 0, 0, 0)
DEFAULT_GT_CSV = (
    Path(__file__).resolve().parent.parent
    / "Simulators"
    / "artificial_week_sensor_dataset_no_user_input.csv"
)


SENSOR_GT_RE = re.compile(
    r"readsensor gt-range dataset_timestamp=(?P<dataset>\S+) "
    r"actual_timestamp=(?P<actual>\S+) "
    r"gt_range=(?P<gt>\S+) min=(?P<min>\d+) max=(?P<max>\d+)"
)
CHANGE_GT_RE = re.compile(
    r"change_lumens gt-range dataset_timestamp=(?P<dataset>\S+) "
    r"actual_timestamp=(?P<actual>\S+) "
    r"applied_lumen=(?P<lumen>-?\d+) gt_range=(?P<gt>\S+) min=(?P<min>\d+) max=(?P<max>\d+)"
)
RESPONSE_RE = re.compile(r"readsensor response=(?P<payload>\{.*\})")

LOG_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)")


def _parse_timestamp(value: str) -> datetime:
    s = value.strip()
    if "T" in s:
        date_part, time_part = s.split("T", 1)
    else:
        date_part, time_part = s.split(" ", 1)

    time_part = time_part.strip().replace(" +", "+")
    if time_part.count("-") == 1:
        time_part = time_part.replace(" -", "-")

    if "." in time_part:
        base, remainder = time_part.split(".", 1)
        frac_digits: list[str] = []
        suffix_start = 0
        for index, char in enumerate(remainder):
            if char.isdigit():
                frac_digits.append(char)
                suffix_start = index + 1
            else:
                break
        frac = "".join(frac_digits)[:6]
        suffix = remainder[suffix_start:]
        time_part = f"{base}.{frac}{suffix}" if frac else f"{base}{suffix}"
    elif "," in time_part:
        # Handle Python logging format: HH:MM:SS,mmm
        time_part = time_part.replace(",", ".")

    parsed = datetime.fromisoformat(f"{date_part}T{time_part}")
    return parsed.replace(tzinfo=None)


def _parse_log_line_timestamp(line: str) -> datetime | None:
    m = LOG_TIMESTAMP_RE.match(line)
    if not m:
        return None
    raw = m.group(1).replace(",", ".")
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def _in_gt_target_range(lumen_value: int, gt_range: tuple[int, int]) -> bool:
    return gt_range[0] <= lumen_value <= gt_range[1]


def _parse_gt_target_range(value: Any) -> tuple[int, int] | None:
    raw = str(value).strip()
    if not raw:
        return None

    if "-" in raw:
        left, right = raw.split("-", 1)
        try:
            low = int(left.strip())
            high = int(right.strip())
        except ValueError:
            return None
    else:
        try:
            low = int(raw)
            high = low
        except ValueError:
            return None

    low = max(0, low)
    high = max(0, high)
    if high < low:
        low, high = high, low
    return low, high


def _mapped_dataset_second_from_actual(actual_timestamp: datetime) -> float:
    elapsed_real_seconds = (actual_timestamp - SIMULATOR_STARTED_AT).total_seconds()
    dataset_elapsed_seconds = elapsed_real_seconds * DATASET_SPEED_FACTOR
    return dataset_elapsed_seconds % LOOP_DATASET_SECONDS


def _load_gt_rows(gt_csv_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gt_csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        raw_rows = list(reader)

    if not raw_rows:
        return rows

    parsed_rows: list[dict[str, Any]] = []
    for row in raw_rows:
        timestamp_text = (row.get("timestamp") or "").strip()
        if not timestamp_text:
            continue
        gt_range = _parse_gt_target_range(row.get("GT - Target"))
        if gt_range is None:
            continue
        parsed_rows.append(
            {
                "dataset_timestamp": datetime.fromisoformat(timestamp_text).replace(tzinfo=None).isoformat(),
                "timestamp": datetime.fromisoformat(timestamp_text).replace(tzinfo=None),
                "gt_target_min": gt_range[0],
                "gt_target_max": gt_range[1],
                "gt_target": f"{gt_range[0]}-{gt_range[1]}",
            }
        )

    parsed_rows.sort(key=lambda x: x["timestamp"])
    if not parsed_rows:
        return rows

    dataset_start = parsed_rows[0]["timestamp"]
    for row in parsed_rows:
        row["dataset_second"] = (row["timestamp"] - dataset_start).total_seconds() % LOOP_DATASET_SECONDS
    return parsed_rows


def _rows_in_dataset_window(
    gt_rows: list[dict[str, Any]],
    start_mapped_second: float,
    end_mapped_second: float,
    full_loop: bool,
) -> list[dict[str, Any]]:
    if full_loop:
        return gt_rows

    if end_mapped_second >= start_mapped_second:
        return [
            row
            for row in gt_rows
            if start_mapped_second < row["dataset_second"] <= end_mapped_second
        ]

    return [
        row
        for row in gt_rows
        if row["dataset_second"] > start_mapped_second
        or row["dataset_second"] <= end_mapped_second
    ]


def _time_sensitive_false_negatives(
    sensors: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    gt_csv_path: Path,
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
) -> list[dict[str, Any]]:
    gt_rows = _load_gt_rows(gt_csv_path)
    if not gt_rows:
        return []

    observed_actual_times: list[datetime] = []
    for sensor in sensors:
        observed_actual_times.append(_parse_timestamp(sensor["actual_timestamp"]))
    for change in changes:
        observed_actual_times.append(_parse_timestamp(change["actual_timestamp"]))
    if not observed_actual_times:
        return []

    effective_start = start_timestamp if start_timestamp is not None else min(observed_actual_times)
    effective_end = end_timestamp if end_timestamp is not None else max(observed_actual_times)
    if effective_end <= effective_start:
        return []

    dataset_duration = (effective_end - effective_start).total_seconds() * DATASET_SPEED_FACTOR
    full_loop = dataset_duration >= LOOP_DATASET_SECONDS
    start_mapped = _mapped_dataset_second_from_actual(effective_start)
    end_mapped = _mapped_dataset_second_from_actual(effective_end)

    expected_rows = _rows_in_dataset_window(gt_rows, start_mapped, end_mapped, full_loop)
    observed_sensor_timestamps = {s["dataset_timestamp"] for s in sensors}
    observed_change_timestamps = {c["dataset_timestamp"] for c in changes}

    missing_rows: list[dict[str, Any]] = []
    for row in expected_rows:
        if row["gt_target_min"] <= 0:
            continue

        dataset_timestamp = row["dataset_timestamp"]
        has_sensor = dataset_timestamp in observed_sensor_timestamps
        has_change = dataset_timestamp in observed_change_timestamps
        if has_sensor and has_change:
            continue

        if not has_sensor and not has_change:
            reason = "missing_sensor_read_and_change_lumens_time_sensitive"
        elif not has_sensor:
            reason = "missing_sensor_read_time_sensitive"
        else:
            reason = "missing_change_lumens_time_sensitive"

        missing_rows.append(
            {
                "event_time": dataset_timestamp,
                "dataset_timestamp": dataset_timestamp,
                "gt_target": row["gt_target"],
                "gt_target_min": row["gt_target_min"],
                "gt_target_max": row["gt_target_max"],
                "reason": reason,
            }
        )

    return missing_rows


def _dedupe_false_negatives(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("dataset_timestamp", "")),
            str(row.get("reason", "")),
            str(row.get("event_time", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def parse_simulator_log(
    log_path: Path,
    start_timestamp: datetime | None,
    end_timestamp: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sensors: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    sensor_by_actual: dict[str, dict[str, Any]] = {}

    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line_ts = _parse_log_line_timestamp(line)
            if start_timestamp is not None and line_ts is not None and line_ts < start_timestamp:
                continue
            if end_timestamp is not None and line_ts is not None and line_ts > end_timestamp:
                continue

            sensor_match = SENSOR_GT_RE.search(line)
            if sensor_match:
                row: dict[str, Any] = {
                    "dataset_timestamp": sensor_match.group("dataset"),
                    "actual_timestamp": sensor_match.group("actual"),
                    "gt_target": sensor_match.group("gt"),
                    "gt_target_min": int(sensor_match.group("min")),
                    "gt_target_max": int(sensor_match.group("max")),
                    "log_timestamp": line_ts.isoformat() if line_ts else sensor_match.group("actual"),
                    "occupancy_count": None,
                    "ambient_light_lux": None,
                    "current_light_lumen": None,
                }
                sensors.append(row)
                sensor_by_actual[row["actual_timestamp"]] = row
                continue

            change_match = CHANGE_GT_RE.search(line)
            if change_match:
                changes.append(
                    {
                        "dataset_timestamp": change_match.group("dataset"),
                        "actual_timestamp": change_match.group("actual"),
                        "gt_target": change_match.group("gt"),
                        "gt_target_min": int(change_match.group("min")),
                        "gt_target_max": int(change_match.group("max")),
                        "applied_lumen": int(change_match.group("lumen")),
                        "log_timestamp": line_ts.isoformat() if line_ts else change_match.group("actual"),
                    }
                )
                continue

            response_match = RESPONSE_RE.search(line)
            if response_match:
                try:
                    payload = ast.literal_eval(response_match.group("payload"))
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                actual_ts = payload.get("actual_timestamp")
                if not isinstance(actual_ts, str):
                    continue
                target = sensor_by_actual.get(actual_ts)
                if target is None:
                    continue
                target["occupancy_count"] = payload.get("occupancy_count")
                target["ambient_light_lux"] = payload.get("ambient_light_lux")
                target["current_light_lumen"] = payload.get("current_light_lumen")

    sensors.sort(key=lambda x: _parse_timestamp(x["actual_timestamp"]))
    changes.sort(key=lambda x: _parse_timestamp(x["actual_timestamp"]))
    return sensors, changes


def evaluate(
    log_path: Path,
    start_timestamp: datetime | None,
    end_timestamp: datetime | None = None,
    time_sensitive: bool = False,
    gt_csv_path: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float]]:
    sensors, changes = parse_simulator_log(log_path, start_timestamp, end_timestamp)

    comparisons: list[dict[str, Any]] = []
    false_negatives: list[dict[str, Any]] = []
    reactivity_seconds: list[float] = []

    # Build a sorted timeline of sensor and change events, then walk it.
    timeline: list[tuple[str, int]] = (
        [("sensor", i) for i in range(len(sensors))]
        + [("change", i) for i in range(len(changes))]
    )
    timeline.sort(
        key=lambda item: _parse_timestamp(
            sensors[item[1]]["actual_timestamp"]
            if item[0] == "sensor"
            else changes[item[1]]["actual_timestamp"]
        )
    )

    pending_sensor_time: str = ""
    pending_sensor_gt_min: int | None = None
    pending_sensor_gt_max: int | None = None
    pending_sensor_gt_target: str | None = None
    pending_sensor_dataset_timestamp: str = ""
    pending_required_change: dict[str, Any] | None = None

    for kind, idx in timeline:
        if kind == "sensor":
            sensor = sensors[idx]

            if pending_required_change is not None:
                false_negatives.append(pending_required_change)
                pending_required_change = None

            pending_sensor_time = sensor["log_timestamp"]
            pending_sensor_gt_min = sensor["gt_target_min"]
            pending_sensor_gt_max = sensor["gt_target_max"]
            pending_sensor_gt_target = sensor["gt_target"]
            pending_sensor_dataset_timestamp = sensor["dataset_timestamp"]
            if pending_sensor_gt_min is not None and pending_sensor_gt_min > 0:
                pending_required_change = {
                    "event_time": sensor["log_timestamp"],
                    "dataset_timestamp": sensor["dataset_timestamp"],
                    "gt_target": sensor["gt_target"],
                    "gt_target_min": pending_sensor_gt_min,
                    "gt_target_max": pending_sensor_gt_max,
                    "reason": "missing_following_change_lumens",
                }
            continue

        # kind == "change"
        change = changes[idx]

        if pending_required_change is not None:
            pending_required_change = None

        reactivity_value: float | None = None
        if pending_sensor_time and change["log_timestamp"]:
            try:
                delta = (
                    _parse_timestamp(change["log_timestamp"])
                    - _parse_timestamp(pending_sensor_time)
                ).total_seconds()
                if delta >= 0:
                    reactivity_value = delta
                    reactivity_seconds.append(delta)
            except ValueError:
                pass
        pending_sensor_time = ""

        gt_min = pending_sensor_gt_min
        gt_max = pending_sensor_gt_max
        lumen = change["applied_lumen"]
        gt_range = (gt_min, gt_max) if gt_min is not None and gt_max is not None else None
        match = _in_gt_target_range(lumen, gt_range) if gt_range is not None else None
        classification = "true_positive" if match is True else ("false_positive" if match is False else "unknown")

        comparisons.append(
            {
                "event_time": change["log_timestamp"],
                "dataset_timestamp": pending_sensor_dataset_timestamp,
                "applied_lumen": lumen,
                "gt_target": pending_sensor_gt_target,
                "gt_target_min": gt_min,
                "gt_target_max": gt_max,
                "match": match,
                "classification": classification,
                "reactivity_seconds": reactivity_value,
            }
        )

    if pending_required_change is not None:
        false_negatives.append(pending_required_change)

    if time_sensitive:
        if gt_csv_path is None:
            gt_csv_path = DEFAULT_GT_CSV
        false_negatives.extend(
            _time_sensitive_false_negatives(
                sensors=sensors,
                changes=changes,
                gt_csv_path=gt_csv_path,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
            )
        )
        false_negatives = _dedupe_false_negatives(false_negatives)

    return comparisons, false_negatives, reactivity_seconds


def print_summary(
    rows: list[dict[str, Any]],
    false_negatives: list[dict[str, Any]],
    reactivity_seconds: list[float],
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
    time_sensitive: bool,
) -> None:
    true_positives = [r for r in rows if r.get("match") is True]
    false_positives = [r for r in rows if r.get("match") is False]
    tp = len(true_positives)
    fp = len(false_positives)
    fn = len(false_negatives)
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    reactivity_avg = (sum(reactivity_seconds) / len(reactivity_seconds)) if reactivity_seconds else 0.0

    print(f"Start timestamp filter: {start_timestamp.isoformat() if start_timestamp else 'none'}")
    print(f"End timestamp filter:   {end_timestamp.isoformat() if end_timestamp else 'none'}")
    print(f"Time-sensitive mode:    {'enabled' if time_sensitive else 'disabled'}")
    print(f"Total change events:   {len(rows)}")
    print(f"True positives:        {tp}")
    print(f"False positives:       {fp}")
    print(f"False negatives:       {fn}")
    print(f"Precision:             {precision:.4f}")
    print(f"Recall:                {recall:.4f}")
    print(f"F1 score:              {f1:.4f}")
    print(f"Average reactivity (s):{reactivity_avg:.4f} over {len(reactivity_seconds)} pairs")

    if false_positives:
        print("\nFirst 20 false positives:")
        for row in false_positives[:20]:
            print(
                f"  {row['event_time']} | lumen={row['applied_lumen']} "
                f"| gt={row['gt_target']} | dataset={row['dataset_timestamp']}"
            )

    if false_negatives:
        print("\nFirst 20 false negatives:")
        for row in false_negatives[:20]:
            print(
                f"  {row['event_time']} | gt={row['gt_target']} "
                f"| dataset={row['dataset_timestamp']} | reason={row['reason']}"
            )


def write_report(rows: list[dict[str, Any]], report_path: Path) -> None:
    fieldnames = [
        "event_time",
        "dataset_timestamp",
        "applied_lumen",
        "gt_target",
        "gt_target_min",
        "gt_target_max",
        "match",
        "classification",
        "reactivity_seconds",
    ]
    with report_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def write_false_negative_report(false_negatives: list[dict[str, Any]], report_path: Path) -> None:
    fieldnames = [
        "event_time",
        "dataset_timestamp",
        "gt_target",
        "gt_target_min",
        "gt_target_max",
        "reason",
    ]
    with report_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in false_negatives:
            writer.writerow({k: row.get(k) for k in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate simulator_static.log against its own embedded ground truth"
    )
    parser.add_argument("log", type=Path, help="Path to simulator_static.log")
    parser.add_argument(
        "--from",
        dest="start",
        default=None,
        metavar="TIMESTAMP",
        help="Only include log lines at or after this timestamp (e.g. 2026-06-03T14:22:00)",
    )
    parser.add_argument(
        "--to",
        dest="end",
        default=None,
        metavar="TIMESTAMP",
        help="Only include log lines before this timestamp (e.g. 2026-06-03T14:22:00)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to write detailed CSV report",
    )
    parser.add_argument(
        "--time-sensitive",
        action="store_true",
        help="Count expected GT CSV events that are missing from the log as false negatives",
    )
    parser.add_argument(
        "--gt_csv",
        type=Path,
        default=DEFAULT_GT_CSV,
        help=(
            "Path to GT CSV used by --time-sensitive "
            f"(default: {DEFAULT_GT_CSV})"
        ),
    )
    args = parser.parse_args()

    start_timestamp: datetime | None = None
    if args.start:
        start_timestamp = _parse_timestamp(args.start)

    end_timestamp: datetime | None = None
    if args.end:
        end_timestamp = _parse_timestamp(args.end)

    rows, false_negatives, reactivity_seconds = evaluate(
        args.log,
        start_timestamp,
        end_timestamp,
        time_sensitive=args.time_sensitive,
        gt_csv_path=args.gt_csv,
    )
    print_summary(
        rows,
        false_negatives,
        reactivity_seconds,
        start_timestamp,
        end_timestamp,
        args.time_sensitive,
    )

    if args.report is not None:
        write_report(rows, args.report)
        print(f"\nWrote report to {args.report}")
        if false_negatives:
            fn_path = args.report.with_name(f"{args.report.stem}.false_negatives.csv")
            write_false_negative_report(false_negatives, fn_path)
            print(f"Wrote false negative report to {fn_path}")


if __name__ == "__main__":
    main()
