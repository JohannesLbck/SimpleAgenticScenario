from __future__ import annotations

import argparse
import ast
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SENSOR_EVENT_NAME = "environment_status"
CHANGE_EVENT_NAME = "change_lumen"
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


def _parse_timestamp(value: str) -> datetime:
    s = value.strip()
    if not s:
        raise ValueError("timestamp is empty")

    try:
        return datetime.fromtimestamp(float(s), tz=timezone.utc).replace(tzinfo=None)
    except ValueError:
        pass

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
        time_part = time_part.replace(",", ".")

    parsed = datetime.fromisoformat(f"{date_part}T{time_part}")
    return parsed.replace(tzinfo=None)


def _to_event(doc: object) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return None
    if "event" in doc and isinstance(doc["event"], dict):
        return doc["event"]
    if "concept:name" in doc:
        return doc
    return None


def _event_timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value if isinstance(value, str) else ""


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


def _in_gt_target_range(lumen_value: int, gt_range: tuple[int, int]) -> bool:
    return gt_range[0] <= lumen_value <= gt_range[1]


def _match_monotonic_by_time(
    source_rows: list[dict[str, Any]],
    source_time_key: str,
    target_rows: list[dict[str, Any]],
    target_time_key: str,
) -> list[int | None]:
    matches: list[int | None] = []
    j = 0

    for src in source_rows:
        src_time = _parse_timestamp(str(src[source_time_key]))
        while j < len(target_rows) and _parse_timestamp(str(target_rows[j][target_time_key])) < src_time:
            j += 1
        if j < len(target_rows):
            matches.append(j)
            j += 1
        else:
            matches.append(None)

    return matches


def parse_agent_mcp_log(
    log_path: Path,
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sensors: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []

    with log_path.open("r", encoding="utf-8") as fh:
        for doc in yaml.safe_load_all(fh):
            event = _to_event(doc)
            if event is None:
                continue

            concept_name = event.get("concept:name")
            transition = event.get("lifecycle:transition")
            if not isinstance(concept_name, str):
                continue
            if not isinstance(transition, str):
                transition = ""

            event_time = _event_timestamp_text(event.get("time:timestamp"))
            if not event_time:
                continue

            parsed_event_time = _parse_timestamp(event_time)
            if start_timestamp is not None and parsed_event_time < start_timestamp:
                continue
            if end_timestamp is not None and parsed_event_time > end_timestamp:
                continue

            if concept_name == SENSOR_EVENT_NAME and transition in ("start", "complete"):
                sensors.append({"event_time": event_time, "transition": transition})
                continue

            if concept_name == CHANGE_EVENT_NAME and transition in ("start", "complete", "activity/calling"):
                changes.append(
                    {
                        "event_time": event_time,
                        "transition": transition,
                        "lumen_sent": _extract_lumen_from_event(event),
                        "concept:name": concept_name,
                    }
                )

    seen_sensor_times: dict[str, dict[str, Any]] = {}
    for row in sensors:
        key = str(row["event_time"])
        existing = seen_sensor_times.get(key)
        if existing is None or existing.get("transition") != "start":
            seen_sensor_times[key] = row

    seen_change_times: dict[str, dict[str, Any]] = {}
    for row in changes:
        key = str(row["event_time"])
        existing = seen_change_times.get(key)
        if existing is None or (existing.get("lumen_sent") is None and row.get("lumen_sent") is not None):
            seen_change_times[key] = row

    deduped_sensors = list(seen_sensor_times.values())
    deduped_changes = list(seen_change_times.values())
    deduped_sensors.sort(key=lambda x: _parse_timestamp(str(x["event_time"])))
    deduped_changes.sort(key=lambda x: _parse_timestamp(str(x["event_time"])))
    return deduped_sensors, deduped_changes


def parse_sensor_log(
    sensor_log_path: Path,
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sensors: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    sensor_by_actual: dict[str, dict[str, Any]] = {}

    with sensor_log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            sensor_match = SENSOR_GT_RE.search(line)
            if sensor_match:
                actual_ts = sensor_match.group("actual")
                actual_dt = _parse_timestamp(actual_ts)
                if start_timestamp is not None and actual_dt < start_timestamp:
                    continue
                if end_timestamp is not None and actual_dt > end_timestamp:
                    continue

                row: dict[str, Any] = {
                    "dataset_timestamp": sensor_match.group("dataset"),
                    "actual_timestamp": actual_ts,
                    "gt_target": sensor_match.group("gt"),
                    "gt_target_min": int(sensor_match.group("min")),
                    "gt_target_max": int(sensor_match.group("max")),
                    "occupancy_count": None,
                    "ambient_light_lux": None,
                    "current_light_lumen": None,
                }
                sensors.append(row)
                sensor_by_actual[actual_ts] = row
                continue

            change_match = CHANGE_GT_RE.search(line)
            if change_match:
                actual_ts = change_match.group("actual")
                actual_dt = _parse_timestamp(actual_ts)
                if start_timestamp is not None and actual_dt < start_timestamp:
                    continue
                if end_timestamp is not None and actual_dt > end_timestamp:
                    continue

                changes.append(
                    {
                        "dataset_timestamp": change_match.group("dataset"),
                        "actual_timestamp": actual_ts,
                        "gt_target": change_match.group("gt"),
                        "gt_target_min": int(change_match.group("min")),
                        "gt_target_max": int(change_match.group("max")),
                        "applied_lumen": int(change_match.group("lumen")),
                    }
                )
                continue

            response_match = RESPONSE_RE.search(line)
            if not response_match:
                continue
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

    sensors.sort(key=lambda x: _parse_timestamp(str(x["actual_timestamp"])))
    changes.sort(key=lambda x: _parse_timestamp(str(x["actual_timestamp"])))
    return sensors, changes


def evaluate(
    agent_log_path: Path,
    sensor_log_path: Path,
    start_timestamp: datetime | None,
    end_timestamp: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float], dict[str, int]]:
    agent_sensors, agent_changes = parse_agent_mcp_log(agent_log_path, start_timestamp, end_timestamp)
    gt_sensors, gt_changes = parse_sensor_log(sensor_log_path, start_timestamp, end_timestamp)

    sensor_matches = _match_monotonic_by_time(agent_sensors, "event_time", gt_sensors, "actual_timestamp")
    change_matches = _match_monotonic_by_time(agent_changes, "event_time", gt_changes, "actual_timestamp")

    for i, match_idx in enumerate(sensor_matches):
        agent_sensors[i]["matched_gt_sensor_idx"] = match_idx
        agent_sensors[i]["matched_gt_sensor"] = gt_sensors[match_idx] if match_idx is not None else None

    comparisons: list[dict[str, Any]] = []
    false_negatives: list[dict[str, Any]] = []
    reactivity_seconds: list[float] = []

    timeline: list[tuple[str, int]] = [
        ("sensor", i) for i in range(len(agent_sensors))
    ] + [
        ("change", i) for i in range(len(agent_changes))
    ]
    timeline.sort(
        key=lambda item: _parse_timestamp(
            str(agent_sensors[item[1]]["event_time"])
            if item[0] == "sensor"
            else str(agent_changes[item[1]]["event_time"])
        )
    )

    pending_sensor_time: str = ""
    pending_required_change: dict[str, Any] | None = None

    for kind, idx in timeline:
        if kind == "sensor":
            sensor = agent_sensors[idx]

            if pending_required_change is not None:
                false_negatives.append(pending_required_change)
                pending_required_change = None

            pending_sensor_time = str(sensor["event_time"])
            matched_sensor = sensor.get("matched_gt_sensor")
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

        change = agent_changes[idx]
        if pending_required_change is not None:
            pending_required_change = None

        reactivity_value: float | None = None
        if pending_sensor_time and change.get("event_time"):
            try:
                delta = (
                    _parse_timestamp(str(change["event_time"]))
                    - _parse_timestamp(pending_sensor_time)
                ).total_seconds()
                if delta >= 0:
                    reactivity_value = delta
                    reactivity_seconds.append(delta)
            except ValueError:
                pass
        pending_sensor_time = ""

        gt_change_idx = change_matches[idx]
        matched_gt_change = gt_changes[gt_change_idx] if gt_change_idx is not None else None

        gt_min = matched_gt_change.get("gt_target_min") if isinstance(matched_gt_change, dict) else None
        gt_max = matched_gt_change.get("gt_target_max") if isinstance(matched_gt_change, dict) else None
        gt_target = matched_gt_change.get("gt_target") if isinstance(matched_gt_change, dict) else None
        dataset_timestamp = matched_gt_change.get("dataset_timestamp") if isinstance(matched_gt_change, dict) else ""

        lumen_sent = change.get("lumen_sent")
        match = None
        classification = "unknown"
        if isinstance(lumen_sent, int) and isinstance(gt_min, int) and isinstance(gt_max, int):
            match = _in_gt_target_range(lumen_sent, (gt_min, gt_max))
            classification = "true_positive" if match else "false_positive"

        comparisons.append(
            {
                "event_time": change.get("event_time"),
                "concept:name": change.get("concept:name", CHANGE_EVENT_NAME),
                "dataset_timestamp": dataset_timestamp,
                "lumen_sent": lumen_sent,
                "gt_target": gt_target,
                "gt_target_min": gt_min,
                "gt_target_max": gt_max,
                "match": match,
                "classification": classification,
                "reactivity_seconds": reactivity_value,
            }
        )

    if pending_required_change is not None:
        false_negatives.append(pending_required_change)

    matching_stats = {
        "agent_sensor_events": len(agent_sensors),
        "matched_sensor_events": sum(1 for i in sensor_matches if i is not None),
        "agent_change_events": len(agent_changes),
        "matched_change_events": sum(1 for i in change_matches if i is not None),
        "gt_sensor_events": len(gt_sensors),
        "gt_change_events": len(gt_changes),
    }

    return comparisons, false_negatives, reactivity_seconds, matching_stats


def print_summary(
    rows: list[dict[str, Any]],
    false_negatives: list[dict[str, Any]],
    reactivity_seconds: list[float],
    matching_stats: dict[str, int],
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
) -> None:
    comparable = [
        row
        for row in rows
        if row.get("lumen_sent") is not None and row.get("gt_target_min") is not None and row.get("gt_target_max") is not None
    ]
    true_positives = [row for row in comparable if row.get("match") is True]
    false_positives = [row for row in comparable if row.get("match") is False]

    tp = len(true_positives)
    fp = len(false_positives)
    fn = len(false_negatives)

    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    reactivity_avg = (sum(reactivity_seconds) / len(reactivity_seconds)) if reactivity_seconds else 0.0

    print(f"Start timestamp filter: {start_timestamp.isoformat() if start_timestamp else 'none'}")
    print(f"End timestamp filter:   {end_timestamp.isoformat() if end_timestamp else 'none'}")
    print(f"Total change events:   {len(rows)}")
    print(f"Comparable events:     {len(comparable)}")
    print(f"True positives:        {tp}")
    print(f"False positives:       {fp}")
    print(f"False negatives:       {fn}")
    print(f"Precision:             {precision:.4f}")
    print(f"Recall:                {recall:.4f}")
    print(f"F1 score:              {f1:.4f}")
    print(f"Average reactivity (s):{reactivity_avg:.4f} over {len(reactivity_seconds)} pairs")

    print("\nMatching stats:")
    for key, value in matching_stats.items():
        print(f"- {key}: {value}")

    if false_positives:
        print("\nFirst 20 false positives:")
        for row in false_positives[:20]:
            print(
                f"  {row['event_time']} | lumen={row['lumen_sent']} "
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
        "concept:name",
        "dataset_timestamp",
        "lumen_sent",
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
        description="Evaluate MCP agent log against GT from simulator sensor log"
    )
    parser.add_argument("agent_log", type=Path, help="Path to MCP-style agent log (e.g. oo3_run_0.log)")
    parser.add_argument("sensor_log", type=Path, help="Path to simulator sensor log (e.g. EvalHelper/simulator_static.log)")
    parser.add_argument(
        "--from",
        dest="start",
        default=None,
        metavar="TIMESTAMP",
        help="Only include events at or after this timestamp",
    )
    parser.add_argument(
        "--to",
        dest="end",
        default=None,
        metavar="TIMESTAMP",
        help="Only include events before this timestamp",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to write detailed CSV report",
    )
    args = parser.parse_args()

    start_timestamp: datetime | None = None
    if args.start:
        start_timestamp = _parse_timestamp(args.start)

    end_timestamp: datetime | None = None
    if args.end:
        end_timestamp = _parse_timestamp(args.end)

    rows, false_negatives, reactivity_seconds, matching_stats = evaluate(
        args.agent_log,
        args.sensor_log,
        start_timestamp,
        end_timestamp,
    )
    print_summary(
        rows,
        false_negatives,
        reactivity_seconds,
        matching_stats,
        start_timestamp,
        end_timestamp,
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
