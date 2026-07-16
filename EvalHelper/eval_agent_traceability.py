from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SENSOR_EVENT_NAMES = {"environment_status", "GetSensor"}
CHANGE_EVENT_NAMES = {"change_lumen", "Change Lumens", "Set lumen to 0", "change lumen"}
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


def _event_in_time_range(
    event_time_str: str,
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
) -> bool:
    if not event_time_str:
        return start_timestamp is None and end_timestamp is None
    try:
        event_time = _parse_timestamp(event_time_str)
    except ValueError:
        return True

    if start_timestamp is not None and event_time < start_timestamp:
        return False
    if end_timestamp is not None and event_time > end_timestamp:
        return False
    return True


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


def _event_data_items(event: dict[str, Any]) -> list[dict[str, Any]]:
    data = event.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _extract_dataset_timestamp(event: dict[str, Any]) -> str | None:
    direct = event.get("dataset_timestamp")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    for item in _event_data_items(event):
        if item.get("name") != "result":
            continue
        payload = item.get("data")
        if isinstance(payload, dict):
            value = payload.get("dataset_timestamp")
            if isinstance(value, str) and value.strip():
                return value.strip()
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            value = parsed.get("dataset_timestamp")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def parse_agent_log(
    agent_log_path: Path,
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    pending_sensor_dataset: str | None = None

    with agent_log_path.open("r", encoding="utf-8") as fh:
        for doc in yaml.safe_load_all(fh):
            event = _to_event(doc)
            if event is None:
                continue

            concept_name = event.get("concept:name")
            if not isinstance(concept_name, str):
                continue

            event_time = _event_timestamp_text(event.get("time:timestamp"))
            if not _event_in_time_range(event_time, start_timestamp, end_timestamp):
                continue

            transition = event.get("cpee:lifecycle:transition")
            if not isinstance(transition, str):
                transition = str(event.get("lifecycle:transition") or "")

            if concept_name in SENSOR_EVENT_NAMES:
                dataset_timestamp = _extract_dataset_timestamp(event)
                if dataset_timestamp:
                    pending_sensor_dataset = dataset_timestamp
                    events.append(
                        {
                            "event_type": "sensor",
                            "dataset_timestamp": dataset_timestamp,
                            "event_time": event_time,
                            "source": "agent",
                        }
                    )
                continue

            if concept_name in CHANGE_EVENT_NAMES and transition in {
                "activity/calling",
                "activity/receiving",
                "activity/complete",
                "start",
                "complete",
            }:
                dataset_timestamp = _extract_dataset_timestamp(event) or pending_sensor_dataset
                if dataset_timestamp:
                    events.append(
                        {
                            "event_type": "change",
                            "dataset_timestamp": dataset_timestamp,
                            "event_time": event_time,
                            "source": "agent",
                        }
                    )

    # Keep one record per identical event signature in the log.
    dedup: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in events:
        key = (row["event_type"], row["dataset_timestamp"], row["event_time"])
        dedup[key] = row

    deduped = list(dedup.values())
    deduped.sort(key=lambda row: _parse_timestamp(row["event_time"]) if row["event_time"] else datetime.min)
    return deduped


def parse_simulator_log(
    simulator_log_path: Path,
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    sensor_by_actual: dict[str, dict[str, str]] = {}

    with simulator_log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            sensor_match = SENSOR_GT_RE.search(line)
            if sensor_match:
                actual_ts = sensor_match.group("actual")
                actual_dt = _parse_timestamp(actual_ts)
                if start_timestamp is not None and actual_dt < start_timestamp:
                    continue
                if end_timestamp is not None and actual_dt > end_timestamp:
                    continue

                row = {
                    "event_type": "sensor",
                    "dataset_timestamp": sensor_match.group("dataset"),
                    "event_time": actual_ts,
                    "source": "simulator",
                }
                events.append(row)
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

                events.append(
                    {
                        "event_type": "change",
                        "dataset_timestamp": change_match.group("dataset"),
                        "event_time": actual_ts,
                        "source": "simulator",
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
            if actual_ts not in sensor_by_actual:
                continue

    events.sort(key=lambda row: _parse_timestamp(row["event_time"]))
    return events


def evaluate_traceability(
    agent_events: list[dict[str, str]],
    simulator_events: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, int]]:
    agent_counter = Counter((row["event_type"], row["dataset_timestamp"]) for row in agent_events)
    sim_counter = Counter((row["event_type"], row["dataset_timestamp"]) for row in simulator_events)

    sim_remaining = sim_counter.copy()
    classified_agent_rows: list[dict[str, str]] = []

    for row in agent_events:
        key = (row["event_type"], row["dataset_timestamp"])
        if sim_remaining[key] > 0:
            classification = "true_positive"
            sim_remaining[key] -= 1
        else:
            classification = "false_positive"

        classified_agent_rows.append(
            {
                "event_type": row["event_type"],
                "dataset_timestamp": row["dataset_timestamp"],
                "event_time": row["event_time"],
                "classification": classification,
            }
        )

    false_negatives: list[dict[str, str]] = []
    for (event_type, dataset_timestamp), count in sim_remaining.items():
        if count <= 0:
            continue
        for _ in range(count):
            false_negatives.append(
                {
                    "event_type": event_type,
                    "dataset_timestamp": dataset_timestamp,
                    "event_time": dataset_timestamp,
                    "reason": "missing_in_agent_log",
                }
            )

    tp = sum(1 for row in classified_agent_rows if row["classification"] == "true_positive")
    fp = sum(1 for row in classified_agent_rows if row["classification"] == "false_positive")
    fn = len(false_negatives)

    stats = {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "agent_total_events": len(agent_events),
        "simulator_total_events": len(simulator_events),
        "agent_unique_event_keys": len(agent_counter),
        "simulator_unique_event_keys": len(sim_counter),
    }
    return classified_agent_rows, false_negatives, stats


def print_summary(
    classified_rows: list[dict[str, str]],
    false_negatives: list[dict[str, str]],
    stats: dict[str, int],
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
) -> None:
    tp = stats["tp"]
    fp = stats["fp"]
    fn = stats["fn"]
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    print(f"Start timestamp filter: {start_timestamp.isoformat() if start_timestamp else 'none'}")
    print(f"End timestamp filter:   {end_timestamp.isoformat() if end_timestamp else 'none'}")
    print(f"Agent total events:     {stats['agent_total_events']}")
    print(f"Simulator total events: {stats['simulator_total_events']}")
    print(f"True positives:         {tp}")
    print(f"False positives:        {fp}")
    print(f"False negatives:        {fn}")
    print(f"Precision:              {precision:.4f}")
    print(f"Recall:                 {recall:.4f}")
    print(f"F1 score:               {f1:.4f}")

    fps = [row for row in classified_rows if row["classification"] == "false_positive"]
    if fps:
        print("\nFirst 20 false positives:")
        for row in fps[:20]:
            print(
                f"  {row['event_time']} | type={row['event_type']} "
                f"| dataset={row['dataset_timestamp']}"
            )

    if false_negatives:
        print("\nFirst 20 false negatives:")
        for row in false_negatives[:20]:
            print(
                f"  {row['event_time']} | type={row['event_type']} "
                f"| dataset={row['dataset_timestamp']} | reason={row['reason']}"
            )


def write_report(rows: list[dict[str, str]], report_path: Path) -> None:
    fieldnames = ["event_time", "event_type", "dataset_timestamp", "classification"]
    with report_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def write_false_negative_report(false_negatives: list[dict[str, str]], report_path: Path) -> None:
    fieldnames = ["event_time", "event_type", "dataset_timestamp", "reason"]
    with report_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in false_negatives:
            writer.writerow({k: row.get(k) for k in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate traceability of agent XES/YAML log against simulator log"
    )
    parser.add_argument("agent_log", type=Path, help="Path to agent XES/YAML log")
    parser.add_argument("simulator_log", type=Path, help="Path to simulator log")
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

    agent_events = parse_agent_log(args.agent_log, start_timestamp, end_timestamp)
    simulator_events = parse_simulator_log(args.simulator_log, start_timestamp, end_timestamp)
    classified_rows, false_negatives, stats = evaluate_traceability(agent_events, simulator_events)

    print_summary(classified_rows, false_negatives, stats, start_timestamp, end_timestamp)

    if args.report is not None:
        write_report(classified_rows, args.report)
        print(f"\nWrote report to {args.report}")
        if false_negatives:
            fn_path = args.report.with_name(f"{args.report.stem}.false_negatives.csv")
            write_false_negative_report(false_negatives, fn_path)
            print(f"Wrote false negative report to {fn_path}")


if __name__ == "__main__":
    main()
