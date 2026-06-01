from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import yaml


ALLOWED_CONCEPT_NAMES = {
    "GetSensor",
    "wait for next iteration",
    "Change Lumens",
    "Set lumen to 0",
}

REMOVED_CPEE_TRANSITIONS = {
    "dataelements/change",
    "activity/done",
}

START_TIME = "2026-06-01T09:53:43.299279+02:00"


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_on_or_after_start_time(event: dict[object, object]) -> bool:
    start_dt = _parse_iso_datetime(START_TIME)
    if start_dt is None:
        return True

    event_dt = _parse_iso_datetime(event.get("time:timestamp"))
    if event_dt is None:
        return True

    try:
        return event_dt >= start_dt
    except TypeError:
        # Handle aware/naive datetime mismatch by comparing naive values.
        return event_dt.replace(tzinfo=None) >= start_dt.replace(tzinfo=None)


def _normalize_header(header_entry: object) -> object:
    if not isinstance(header_entry, dict):
        return header_entry

    log_section = header_entry.get("log")
    if not isinstance(log_section, dict):
        return header_entry

    global_section = log_section.get("global")
    if isinstance(global_section, dict):
        global_section.pop("event", None)

    return header_entry


def _select_event(event: object) -> object | None:
    if not isinstance(event, dict):
        return None

    event = event.get("event")
    if not isinstance(event, dict):
        return None

    if not _is_on_or_after_start_time(event):
        return None

    cpee_transition = event.get("cpee:lifecycle:transition")
    if cpee_transition in REMOVED_CPEE_TRANSITIONS:
        return None

    if event.get("concept:name") not in ALLOWED_CONCEPT_NAMES:
        return None

    if cpee_transition == "activity/receiving" and event.get("lifecycle:transition") == "unknown":
        event["lifecycle:transition"] = "complete"

    return event


def filter_xes_yaml(input_path: Path, output_path: Path) -> tuple[int, int]:
    kept_events = 0
    total_events = 0

    with input_path.open("r", encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        full_log = yaml.safe_load_all(src)
        first_event = True

        for event in full_log:
            if first_event:
                yaml.safe_dump(
                    _normalize_header(event),
                    dst,
                    sort_keys=False,
                    explicit_start=True,
                    allow_unicode=False,
                )
                first_event = False
                continue

            if not isinstance(event, dict) or "event" not in event:
                continue

            total_events += 1
            filtered = _select_event(event)
            if filtered is None:
                continue

            yaml.safe_dump(
                filtered,
                dst,
                sort_keys=False,
                explicit_start=True,
                allow_unicode=False,
            )
            kept_events += 1

    return kept_events, total_events


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter XES-YAML logs by concept:name")
    parser.add_argument("input", help="Path to the input .xes.yaml log")
    parser.add_argument(
        "output",
        nargs="?",
        help="Path to the filtered output log (default: <input>.filtered.xes.yaml)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if args.output:
        output_path = Path(args.output)
    else:
        if input_path.suffixes[-2:] == [".xes", ".yaml"]:
            output_path = input_path.with_name(input_path.name.replace(".xes.yaml", ".filtered.xes.yaml"))
        else:
            output_path = input_path.with_name(f"{input_path.name}.filtered")

    kept_events, total_events = filter_xes_yaml(input_path, output_path)
    print(f"Filtered {input_path} -> {output_path}")
    print(f"Kept {kept_events} of {total_events} event documents")


if __name__ == "__main__":
    main()