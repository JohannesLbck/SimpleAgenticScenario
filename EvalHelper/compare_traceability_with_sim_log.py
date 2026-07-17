from __future__ import annotations

import argparse
import ast
import csv
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SENSORCALLLABEL = "GetSensor"
LUMENCHANGELABEL = "Change Lumens"
LUMENZEROINGLABEL = "Set lumen to 0"
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


def parse_agent_log(
    log_path: Path,
) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    event_order = 0

    with log_path.open("r", encoding="utf-8") as fh:
        for doc in yaml.safe_load_all(fh):
            event = _to_event(doc)
            if event is None:
                continue

            concept_name = event.get("concept:name")
            if not isinstance(concept_name, str):
                continue

            event_time_str = _event_timestamp_text(event.get("time:timestamp"))
            if not event_time_str:
                continue

            cpee_transition = event.get("cpee:lifecycle:transition")

            if concept_name == SENSORCALLLABEL and cpee_transition in {"activity/receiving", "activity/complete"}:
                event_order += 1
                events.append(
                    {
                        "event_type": "sensor",
                        "event_order": str(event_order),
                        "event_time": event_time_str,
                        "source": "agent",
                    }
                )
                continue

            if concept_name in (LUMENCHANGELABEL, LUMENZEROINGLABEL) and cpee_transition == "activity/calling":
                if event_order > 0:
                    event_order += 1
                    events.append(
                        {
                            "event_type": "change",
                            "event_order": str(event_order),
                            "event_time": event_time_str,
                            "source": "agent",
                        }
                    )

    dedup: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in events:
        key = (row["event_type"], row["event_order"], row["event_time"])
        dedup[key] = row

    deduped = list(dedup.values())
    deduped.sort(key=lambda row: _parse_timestamp(row["event_time"]) if row["event_time"] else datetime.min)
    return deduped


def parse_simulator_log(
    simulator_log_path: Path,
) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    sensor_by_actual: dict[str, dict[str, str]] = {}
    event_order = 0

    with simulator_log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            sensor_match = SENSOR_GT_RE.search(line)
            if sensor_match:
                actual_ts = sensor_match.group("actual")
                try:
                    _parse_timestamp(actual_ts)
                except ValueError:
                    continue

                event_order += 1
                row = {
                    "event_type": "sensor",
                    "event_order": str(event_order),
                    "event_time": actual_ts,
                    "source": "simulator",
                }
                events.append(row)
                sensor_by_actual[actual_ts] = row
                continue

            change_match = CHANGE_GT_RE.search(line)
            if change_match:
                actual_ts = change_match.group("actual")
                try:
                    _parse_timestamp(actual_ts)
                except ValueError:
                    continue

                event_order += 1
                events.append(
                    {
                        "event_type": "change",
                        "event_order": str(event_order),
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


def _derive_comparison_window(
    agent_events: list[dict[str, str]],
) -> tuple[datetime | None, datetime | None]:
    if not agent_events:
        return None, None

    start_timestamp = _parse_timestamp(agent_events[0]["event_time"])
    end_timestamp = _parse_timestamp(agent_events[-1]["event_time"])
    if end_timestamp < start_timestamp:
        start_timestamp, end_timestamp = end_timestamp, start_timestamp
    return start_timestamp, end_timestamp


def _filter_events_to_window(
    events: list[dict[str, str]],
    start_timestamp: datetime | None,
    end_timestamp: datetime | None,
) -> list[dict[str, str]]:
    if start_timestamp is None or end_timestamp is None:
        return []
    return [
        row
        for row in events
        if _event_in_time_range(str(row.get("event_time", "")), start_timestamp, end_timestamp)
    ]


def evaluate_traceability(
    agent_events: list[dict[str, str]],
    simulator_events: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, int]]:
    agent_counter = Counter((row["event_type"], row["event_order"]) for row in agent_events)
    sim_counter = Counter((row["event_type"], row["event_order"]) for row in simulator_events)
    sim_remaining = sim_counter.copy()

    classified_rows: list[dict[str, str]] = []
    for row in agent_events:
        key = (row["event_type"], row["event_order"])
        if sim_remaining[key] > 0:
            classification = "true_positive"
            sim_remaining[key] -= 1
        else:
            classification = "false_positive"

        classified_rows.append(
            {
                "event_type": row["event_type"],
                "event_order": row["event_order"],
                "event_time": row["event_time"],
                "classification": classification,
            }
        )

    false_negatives: list[dict[str, str]] = []
    for (event_type, event_order), count in sim_remaining.items():
        if count <= 0:
            continue
        for _ in range(count):
            false_negatives.append(
                {
                    "event_type": event_type,
                    "event_order": event_order,
                    "event_time": "",
                    "reason": "missing_in_agent_log",
                }
            )

    tp = sum(1 for row in classified_rows if row["classification"] == "true_positive")
    fp = sum(1 for row in classified_rows if row["classification"] == "false_positive")
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
    return classified_rows, false_negatives, stats


def print_summary(
    rows: list[dict[str, str]],
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

    print(f"Comparison window start: {start_timestamp.isoformat() if start_timestamp else 'none'}")
    print(f"Comparison window end:   {end_timestamp.isoformat() if end_timestamp else 'none'}")
    print(f"Total agent events:     {stats['agent_total_events']}")
    print(f"Total simulator events: {stats['simulator_total_events']}")
    print(f"Agent unique keys:      {stats['agent_unique_event_keys']}")
    print(f"Simulator unique keys:  {stats['simulator_unique_event_keys']}")
    print(f"True positives:         {tp}")
    print(f"False positives:        {fp}")
    print(f"False negatives:        {fn}")
    print(f"Precision:              {precision:.4f}")
    print(f"Recall:                 {recall:.4f}")
    print(f"F1 score:               {f1:.4f}")

    false_positives = [row for row in rows if row["classification"] == "false_positive"]
    if false_positives:
        print("\nFirst 20 false positives:")
        for row in false_positives[:20]:
            print(f"  {row['event_time']} | type={row['event_type']} | order={row['event_order']}")

    if false_negatives:
        print("\nFirst 20 false negatives:")
        for row in false_negatives[:20]:
            print(f"  order={row['event_order']} | type={row['event_type']} | reason={row['reason']}")

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare traceability between XES/YAML agent log and simulator log"
    )
    parser.add_argument("log", type=Path, help="Path to filtered log (.xes.yaml)")
    parser.add_argument("simulator_log", type=Path, help="Path to simulator_static.log")
    args = parser.parse_args()

    agent_events_all = parse_agent_log(args.log)
    simulator_events_all = parse_simulator_log(args.simulator_log)
    start_timestamp, end_timestamp = _derive_comparison_window(agent_events_all)
    agent_events = _filter_events_to_window(agent_events_all, start_timestamp, end_timestamp)
    simulator_events = _filter_events_to_window(simulator_events_all, start_timestamp, end_timestamp)
    rows, false_negatives, stats = evaluate_traceability(agent_events, simulator_events)
    print_summary(rows, false_negatives, stats, start_timestamp, end_timestamp)

if __name__ == "__main__":
    main()
