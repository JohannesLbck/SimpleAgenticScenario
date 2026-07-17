import argparse
from datetime import datetime
import re
from pathlib import Path
import yaml

EVENTTYPES = [
    "change lumen",
    "read sensor",
]
EVENTNAMEALIASES = {
    "change lumen": ["changelumen", "change_lumen", "change_lumens", "changelumens"],
    "read sensor": ["readsensor", "read_sensor", "read_sensors", "getsensor"],
}

SENSOR_APP_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+\w+\s+(?P<event>[a-zA-Z_][\w-]*)"
)
SENSOR_HTTP_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)?.*\"[A-Z]+\s+(?P<event>/[\w-]+)\s+HTTP/"
)

TIMESTAMP_SPLIT_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?P<fraction>[.,]\d+)?(?P<tz>Z|[+-]\d{2}:\d{2})?$"
)


def _sanitize_timestamp_input(timestamp: str | None) -> str | None:
    if timestamp is None:
        return None

    value = timestamp.strip()
    if not value:
        return None

    value = value.replace(",", ".")
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"

    # Fast path: let datetime handle already-valid ISO timestamps.
    try:
        datetime.fromisoformat(value)
        return value
    except ValueError:
        pass

    # Handle nanosecond-like precision by truncating/padding to microseconds.
    match = TIMESTAMP_SPLIT_RE.match(value)
    if not match:
        return None

    base = match.group("base")
    fraction = match.group("fraction") or ""
    tz = match.group("tz") or ""

    if fraction:
        digits = fraction[1:]
        normalized_fraction = f".{(digits + '000000')[:6]}"
    else:
        normalized_fraction = ""

    return f"{base}{normalized_fraction}{tz}"


def normalize_timestamp(timestamp: str | None) -> str | None:
    """Normalize timestamps to 'YYYY-mm-dd HH:MM:SS.ffffff' without tz info."""
    dt = parse_timestamp(timestamp)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def parse_timestamp(timestamp: str | None) -> datetime | None:
    sanitized = _sanitize_timestamp_input(timestamp)
    if not sanitized:
        return None
    try:
        parsed = datetime.fromisoformat(sanitized)
        # Keep wall-clock comparisons stable by using naive datetimes everywhere.
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed
    except ValueError:
        return None



def read_xes_yaml_log(file_path: str, start_timestamp: str | None = None, end_timestamp: str | None = None) -> tuple[int, int]:
    
    lumen_events = 0
    sensor_events = 0
    start_dt = parse_timestamp(start_timestamp)
    end_dt = parse_timestamp(end_timestamp)

    path = Path(file_path)
    with path.open("r", encoding="utf-8") as fh:
        for event in yaml.safe_load_all(fh):
            event = event.get("event")
            event_dt = parse_timestamp(str(event.get("time:timestamp")))
            if start_dt and event_dt and event_dt < start_dt:
                continue
            if end_dt and event_dt and event_dt > end_dt:
                continue
            try:
                event["lifecycle:transition"]
            except TypeError:
                continue
            if event["lifecycle:transition"] != "complete":
                continue
            concept_name = event.get("concept:name", "").replace(" ", "").lower()
            if concept_name in EVENTNAMEALIASES["change lumen"]:
                lumen_events += 1
            elif concept_name in EVENTNAMEALIASES["read sensor"]:
                sensor_events += 1
    return lumen_events, sensor_events
                
    
def read_sensor_log(file_path: str, start_timestamp: str | None = None, end_timestamp: str | None = None) -> tuple[int, int]:
    lumen_events = 0
    sensor_events = 0
    start_dt = parse_timestamp(start_timestamp)
    end_dt = parse_timestamp(end_timestamp)

    def parse_line_timestamp_and_event(line: str) -> tuple[str | None, str | None]:
        app_match = SENSOR_APP_LINE_RE.search(line)
        if app_match:
            timestamp = app_match.group("timestamp")
            event = app_match.group("event")
            # Normalize app events to endpoint-style names like /readsensor.
            if event and not event.startswith("/"):
                event = f"/{event}"
            return timestamp, event

        http_match = SENSOR_HTTP_LINE_RE.search(line)
        if http_match:
            return http_match.group("timestamp"), http_match.group("event")

        return None, None
    
    path = Path(file_path)
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            timestamp, event = parse_line_timestamp_and_event(line)
            if event is None:
                continue
            timestamp_dt = parse_timestamp(timestamp)
            if start_dt and timestamp_dt and timestamp_dt < start_dt:
                continue
            if end_dt and timestamp_dt and timestamp_dt > end_dt:
                continue

            normalized_event = event.lstrip("/").replace(" ", "").lower()
            if normalized_event in EVENTNAMEALIASES["change lumen"]:
                lumen_events += 1
            elif normalized_event in EVENTNAMEALIASES["read sensor"]:
                sensor_events += 1
    return lumen_events, sensor_events



def main():
    parser = argparse.ArgumentParser(description="Evaluate the traceability of an agent's actions.")
    parser.add_argument(
        "agent_log",
        type=str,
        help="Path to the agent log file to evaluate",
    )
    parser.add_argument(
        "sensor_log",
        type=str,
        help="Path to the sensor log file to compare with",
    )
    parser.add_argument(
        "--start-timestamp",
        type=str,
        default=None,
        metavar="TIMESTAMP",
        help="Only include events after this timestamp",
    )
    parser.add_argument(
        "--end-timestamp",
        type=str,
        default=None,
        metavar="TIMESTAMP",
        help="Only include events before this timestamp",
    )

    args = parser.parse_args() 
    
    xes_log = read_xes_yaml_log(args.agent_log, start_timestamp=args.start_timestamp, end_timestamp=args.end_timestamp)
    sensor_log = read_sensor_log(args.sensor_log, start_timestamp=args.start_timestamp, end_timestamp=args.end_timestamp)
    
    print(f"Xes_Log: (Lumen, Sensor) {xes_log}")
    print(f"Sensor_Log: (Lumen, Sensor) {sensor_log}")
    
if __name__ == "__main__":
    main()
