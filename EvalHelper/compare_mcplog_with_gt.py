from __future__ import annotations

import argparse
import ast
import csv
from datetime import datetime
import re
from pathlib import Path
from typing import Any

import yaml


SENSORCALLLABEL = "environment_status"
LUMENCHANGELABEL = "change_lumen"
STARTTIMESTAMP = "2026-06-02T09:51:23"

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


def _to_event(doc: object) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return None
    if "event" in doc and isinstance(doc["event"], dict):
        return doc["event"]
    if "concept:name" in doc:
        return doc
    return None


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip().strip('"').strip("'")
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _event_data_items(event: dict[str, Any]) -> list[dict[str, Any]]:
    data = event.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _extract_lumen_from_event(event: dict[str, Any]) -> int | None:
    for item in _event_data_items(event):
        if item.get("name") == "lumen":
            return _parse_int(item.get("value"))
    return None


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

    parsed = datetime.fromisoformat(f"{date_part}T{time_part}")
    return parsed.replace(tzinfo=None)


def _event_timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value if isinstance(value, str) else ""


def _timestamp_on_or_after(value: str, start: str) -> bool:
    try:
        return _parse_timestamp(value) >= _parse_timestamp(start)
    except ValueError:
        return value >= start


def _in_gt_target_range(lumen_value: int, gt_range: tuple[int, int]) -> bool:
    return gt_range[0] <= lumen_value <= gt_range[1]


def parse_mcp_log(log_path: Path, use_timefilter: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sensors: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []

    with log_path.open("r", encoding="utf-8") as fh:
        for doc in yaml.safe_load_all(fh):
            event = _to_event(doc)
            if event is None:
                continue

            concept_name = event.get("concept:name")
            transition = event.get("lifecycle:transition")
            if not isinstance(concept_name, str) or not isinstance(transition, str):
                continue

            event_time = _event_timestamp_text(event.get("time:timestamp"))
            if not event_time:
                continue

            if use_timefilter and not _timestamp_on_or_after(event_time, STARTTIMESTAMP):
                continue

            if concept_name == SENSORCALLLABEL and transition in ("start", "complete"):
                sensors.append({"event_time": event_time, "transition": transition})
            elif concept_name == LUMENCHANGELABEL and transition in ("start", "complete"):
                changes.append(
                    {
                        "event_time": event_time,
                        "lumen_sent": _extract_lumen_from_event(event),
                        "concept:name": concept_name,
                    }
                )

    # Deduplicate: for logs that have both start and complete, keep one per event.
    # Group by event_time; prefer "start" over "complete" for sensors.
    seen_sensor_times: dict[str, dict[str, Any]] = {}
    for s in sensors:
        t = s["event_time"]
        existing = seen_sensor_times.get(t)
        if existing is None or existing.get("transition") != "start":
            seen_sensor_times[t] = s
    sensors = sorted(seen_sensor_times.values(), key=lambda x: _parse_timestamp(x["event_time"]))

    # Changes: prefer "start" (carries lumen value); if only "complete" exists use that.
    seen_change_times: dict[str, dict[str, Any]] = {}
    for c in changes:
        t = c["event_time"]
        existing = seen_change_times.get(t)
        if existing is None or (existing.get("lumen_sent") is None and c.get("lumen_sent") is not None):
            seen_change_times[t] = c
    changes = sorted(seen_change_times.values(), key=lambda x: _parse_timestamp(x["event_time"]))

    return sensors, changes


def parse_simulator_static_log(log_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sensors: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    sensor_by_actual: dict[str, dict[str, Any]] = {}

    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            sensor_match = SENSOR_GT_RE.search(line)
            if sensor_match:
                row = {
                    "dataset_timestamp": sensor_match.group("dataset"),
                    "actual_timestamp": sensor_match.group("actual"),
                    "gt_target": sensor_match.group("gt"),
                    "gt_target_min": int(sensor_match.group("min")),
                    "gt_target_max": int(sensor_match.group("max")),
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
                    }
                )
                continue

            response_match = RESPONSE_RE.search(line)
            if response_match:
                payload_text = response_match.group("payload")
                try:
                    payload = ast.literal_eval(payload_text)
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


def _match_monotonic_by_time(
    source_rows: list[dict[str, Any]],
    source_time_key: str,
    target_rows: list[dict[str, Any]],
    target_time_key: str,
) -> list[int | None]:
    matches: list[int | None] = []
    j = 0

    for src in source_rows:
        src_time = _parse_timestamp(src[source_time_key])
        while j < len(target_rows) and _parse_timestamp(target_rows[j][target_time_key]) < src_time:
            j += 1
        if j < len(target_rows):
            matches.append(j)
            j += 1
        else:
            matches.append(None)

    return matches


def compare_mcp_with_simulator_gt(
    mcp_log_path: Path,
    simulator_log_path: Path,
    use_timefilter: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float], dict[str, int]]:
    mcp_sensors, mcp_changes = parse_mcp_log(mcp_log_path, use_timefilter)
    sim_sensors, sim_changes = parse_simulator_static_log(simulator_log_path)

    sensor_matches = _match_monotonic_by_time(mcp_sensors, "event_time", sim_sensors, "actual_timestamp")
    change_matches = _match_monotonic_by_time(mcp_changes, "event_time", sim_changes, "actual_timestamp")

    for i, match_idx in enumerate(sensor_matches):
        mcp_sensors[i]["matched_sim_sensor_idx"] = match_idx
        mcp_sensors[i]["matched_sim_sensor"] = sim_sensors[match_idx] if match_idx is not None else None

    comparisons: list[dict[str, Any]] = []
    false_negatives: list[dict[str, Any]] = []
    reactivity_seconds: list[float] = []

    # Re-run chronologically for reactivity + comparisons, same spirit as original script.
    sensor_ptr = 0
    change_ptr = 0
    pending_sensor_time = ""
    pending_required_change = None

    timeline: list[tuple[str, int]] = []
    for idx, row in enumerate(mcp_sensors):
        timeline.append(("sensor", idx))
    for idx, row in enumerate(mcp_changes):
        timeline.append(("change", idx))
    timeline.sort(
        key=lambda item: _parse_timestamp(
            mcp_sensors[item[1]]["event_time"] if item[0] == "sensor" else mcp_changes[item[1]]["event_time"]
        )
    )

    for kind, idx in timeline:
        if kind == "sensor":
            sensor_ptr = idx
            sensor = mcp_sensors[sensor_ptr]
            if pending_required_change is not None:
                false_negatives.append(pending_required_change)
                pending_required_change = None

            pending_sensor_time = sensor["event_time"]
            matched_sensor = sensor.get("matched_sim_sensor")
            if isinstance(matched_sensor, dict):
                gt_min = matched_sensor.get("gt_target_min")
                gt_max = matched_sensor.get("gt_target_max")
                if isinstance(gt_min, int) and isinstance(gt_max, int) and gt_min > 0:
                    pending_required_change = {
                        "event_time": sensor["event_time"],
                        "dataset_timestamp": matched_sensor.get("dataset_timestamp"),
                        "gt_target": matched_sensor.get("gt_target"),
                        "gt_target_min": gt_min,
                        "gt_target_max": gt_max,
                        "reason": "missing_following_change_lumens",
                    }
            continue

        change_ptr = idx
        change = mcp_changes[change_ptr]
        if pending_required_change is not None:
            pending_required_change = None

        reactivity_value: float | None = None
        if pending_sensor_time and change["event_time"]:
            try:
                delta = (_parse_timestamp(change["event_time"]) - _parse_timestamp(pending_sensor_time)).total_seconds()
                if delta >= 0:
                    reactivity_value = delta
                    reactivity_seconds.append(delta)
            except ValueError:
                pass
        pending_sensor_time = ""

        sim_change_match_idx = change_matches[change_ptr]
        matched_sim_change = sim_changes[sim_change_match_idx] if sim_change_match_idx is not None else None

        gt_min = matched_sim_change.get("gt_target_min") if isinstance(matched_sim_change, dict) else None
        gt_max = matched_sim_change.get("gt_target_max") if isinstance(matched_sim_change, dict) else None
        gt_target = matched_sim_change.get("gt_target") if isinstance(matched_sim_change, dict) else None
        dataset_timestamp = matched_sim_change.get("dataset_timestamp") if isinstance(matched_sim_change, dict) else ""

        lumen_sent = change.get("lumen_sent")
        match = None
        classification = "unknown"
        if isinstance(lumen_sent, int) and isinstance(gt_min, int) and isinstance(gt_max, int):
            match = _in_gt_target_range(lumen_sent, (gt_min, gt_max))
            classification = "true_positive" if match else "false_positive"

        comparisons.append(
            {
                "event_time": change["event_time"],
                "concept:name": change.get("concept:name", LUMENCHANGELABEL),
                "activity_uuid": "",
                "dataset_timestamp": dataset_timestamp,
                "occupancy_count": None,
                "ambient_light_lux": None,
                "current_light_lumen": None,
                "lumen_sent": lumen_sent,
                "gt_target": gt_target,
                "gt_target_min": gt_min,
                "gt_target_max": gt_max,
                "match": match,
                "classification": classification,
                "reactivity_seconds": reactivity_value,
                "lumen_source": "mcp-start-data" if lumen_sent is not None else "unknown",
            }
        )

    if pending_required_change is not None:
        false_negatives.append(pending_required_change)

    matching_stats = {
        "mcp_sensor_events": len(mcp_sensors),
        "matched_sensor_events": sum(1 for i in sensor_matches if i is not None),
        "mcp_change_events": len(mcp_changes),
        "matched_change_events": sum(1 for i in change_matches if i is not None),
        "sim_sensor_events": len(sim_sensors),
        "sim_change_events": len(sim_changes),
    }

    return comparisons, false_negatives, reactivity_seconds, matching_stats


def print_summary(rows: list[dict[str, Any]], false_negatives: list[dict[str, Any]], reactivity_seconds: list[float], matching_stats: dict[str, int]) -> None:
    comparable = [
        r
        for r in rows
        if r.get("lumen_sent") is not None and r.get("gt_target_min") is not None and r.get("gt_target_max") is not None
    ]
    true_positives = [r for r in comparable if r.get("match") is True]
    false_positives = [r for r in comparable if r.get("match") is False]
    missing_lumen = [r for r in rows if r.get("lumen_sent") is None]
    missing_gt = [r for r in rows if r.get("gt_target") is None]

    tp = len(true_positives)
    fp = len(false_positives)
    fn = len(false_negatives)
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    reactivity_avg = (sum(reactivity_seconds) / len(reactivity_seconds)) if reactivity_seconds else 0.0

    print(f"Total lumen events: {len(rows)}")
    print(f"Comparable events: {len(comparable)}")
    print(f"True positives: {tp}")
    print(f"False positives: {fp}")
    print(f"False negatives: {fn}")
    print(f"Missing lumen value: {len(missing_lumen)}")
    print(f"Missing GT target: {len(missing_gt)}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall: {recall:.4f}")
    print(f"F1 score: {f1:.4f}")
    print(f"Average reactivity (s): {reactivity_avg:.4f} over {len(reactivity_seconds)} pairs")

    print("\nMatching stats:")
    for key, value in matching_stats.items():
        print(f"- {key}: {value}")

    if false_positives:
        print("\nFirst 20 false positives:")
        for row in false_positives[:20]:
            print(
                f"- {row['event_time']} | {row['concept:name']} | "
                f"dataset={row['dataset_timestamp']} | lumen={row['lumen_sent']} | gt_range={row['gt_target']}"
            )

    if false_negatives:
        print("\nFirst 20 false negatives:")
        for row in false_negatives[:20]:
            print(
                f"- {row['event_time']} | {SENSORCALLLABEL} start | "
                f"dataset={row['dataset_timestamp']} | gt_range={row['gt_target']} | reason={row['reason']}"
            )


def write_report(rows: list[dict[str, Any]], report_path: Path) -> None:
    fieldnames = [
        "event_time",
        "concept:name",
        "activity_uuid",
        "dataset_timestamp",
        "occupancy_count",
        "ambient_light_lux",
        "current_light_lumen",
        "lumen_sent",
        "gt_target",
        "gt_target_min",
        "gt_target_max",
        "match",
        "classification",
        "reactivity_seconds",
        "lumen_source",
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
        description="Compare mcp.log events against simulator_static.log GT ranges"
    )
    parser.add_argument("mcp_log", type=Path, help="Path to mcp.log")
    parser.add_argument("simulator_log", type=Path, help="Path to simulator_static.log")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to write detailed CSV report",
    )
    parser.add_argument(
        "--timefilter",
        action="store_true",
        help=f"Apply STARTTIMESTAMP filter ({STARTTIMESTAMP}) to mcp timestamps",
    )
    args = parser.parse_args()

    rows, false_negatives, reactivity_seconds, matching_stats = compare_mcp_with_simulator_gt(
        args.mcp_log,
        args.simulator_log,
        use_timefilter=args.timefilter,
    )
    print_summary(rows, false_negatives, reactivity_seconds, matching_stats)

    if args.report is not None:
        write_report(rows, args.report)
        print(f"\nWrote report to {args.report}")
        if false_negatives:
            fn_report_path = args.report.with_name(f"{args.report.stem}.false_negatives.csv")
            write_false_negative_report(false_negatives, fn_report_path)
            print(f"Wrote false negative report to {fn_report_path}")


if __name__ == "__main__":
    main()
