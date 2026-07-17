import argparse
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



def read_xes_yaml_log(file_path: str, start_timestamp: str = None, end_timestamp: str = None) -> tuple[int, int]:
    
    lumen_events = 0
    sensor_events = 0

    path = Path(file_path)
    with path.open("r", encoding="utf-8") as fh:
        for event in yaml.safe_load_all(fh):
            event = event.get("event")
            if start_timestamp and event.get("time:timestamp") < start_timestamp:
                continue
            if end_timestamp and event.get("time:timestamp") > end_timestamp:
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
                
    
def read_sensor_log(file_path: str, start_timestamp: str = None, end_timestamp: str = None) -> tuple[int, int]:
    lumen_events = 0
    sensor_events = 0

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
            if start_timestamp and timestamp and timestamp < start_timestamp:
                continue
            if end_timestamp and timestamp and timestamp > end_timestamp:
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