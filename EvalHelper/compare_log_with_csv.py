from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import yaml


SENSORCALLLABEL = "GetSensor"
TIMEOUTLABEL = "wait for next iteration"
LUMENCHANGELABEL = "Change Lumens"
LUMENZEROINGLABEL = "Set lumen to 0"
STARTDATASETTIMESTAMP = "2026-05-18T00:00:00"
LUMEN_PER_LUX = 2 * math.pi * (3.0**2) * (1.0 - math.cos(math.radians(30.0)))


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
		s = value.strip().strip("\"").strip("'")
		if not s:
			return None
		try:
			return int(s)
		except ValueError:
			return None
	return None


def _parse_gt_target_range(value: Any) -> tuple[int, int] | None:
	if isinstance(value, bool):
		return None
	if isinstance(value, int):
		v = max(0, value)
		return (v, v)
	if isinstance(value, float):
		v = max(0, int(value))
		return (v, v)
	if not isinstance(value, str):
		return None

	s = value.strip()
	if not s:
		return None

	if "-" in s:
		left, right = s.split("-", 1)
		low = _parse_int(left)
		high = _parse_int(right)
		if low is None or high is None:
			return None
		low = max(0, low)
		high = max(0, high)
		return (low, high) if low <= high else (high, low)

	v = _parse_int(s)
	if v is None:
		return None
	v = max(0, v)
	return (v, v)


def _in_gt_target_range(lumen_value: int, gt_range: tuple[int, int]) -> bool:
	return gt_range[0] <= lumen_value <= gt_range[1]


def _parse_float(value: Any, default: float = 0.0) -> float:
	try:
		return float(str(value).strip())
	except Exception:
		return default


def _parse_bool(value: Any) -> bool:
	return str(value).strip().lower() in {"true", "1", "yes"}


def _time_of_day_target(hour: float, occupancy: int, movement: bool) -> tuple[float, float]:
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


def _lux_to_lumen_range(target_low_lux: float, target_high_lux: float, ambient_lux: float) -> tuple[int, int]:
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


def _compute_expected_gt_range(row: dict[str, str]) -> tuple[int, int]:
	ambient_lux = _parse_float(row.get("ambient_light_lux", 0.0))
	occupancy = _parse_int(row.get("occupancy_count", 0)) or 0
	movement = _parse_bool(row.get("motion_detected", False))
	hour = _parse_float(row.get("hour", 0.0))
	target_low_lux, target_high_lux = _time_of_day_target(hour, occupancy, movement)
	return _lux_to_lumen_range(target_low_lux, target_high_lux, ambient_lux)


def _event_data_items(event: dict[str, Any]) -> list[dict[str, Any]]:
	data = event.get("data")
	if isinstance(data, list):
		return [item for item in data if isinstance(item, dict)]
	return []


def _extract_dataset_timestamp_from_getsensor(event: dict[str, Any]) -> str | None:
	for item in _event_data_items(event):
		if item.get("name") != "result":
			continue
		payload = item.get("data")
		if isinstance(payload, dict):
			value = payload.get("dataset_timestamp")
			return value if isinstance(value, str) else None
		if isinstance(payload, str):
			try:
				parsed = json.loads(payload)
			except json.JSONDecodeError:
				continue
			value = parsed.get("dataset_timestamp")
			return value if isinstance(value, str) else None
	return None


def _extract_lumen_from_call(event: dict[str, Any]) -> int | None:
	for item in _event_data_items(event):
		if item.get("name") != "lumen":
			continue
		return _parse_int(item.get("value"))
	return None


def _extract_applied_lumen_from_receive(event: dict[str, Any]) -> int | None:
	for item in _event_data_items(event):
		if item.get("name") != "result":
			continue
		payload = item.get("data")
		if isinstance(payload, dict):
			return _parse_int(payload.get("applied_lumen"))
		if isinstance(payload, str):
			try:
				parsed = json.loads(payload)
			except json.JSONDecodeError:
				continue
			return _parse_int(parsed.get("applied_lumen"))
	return None


def load_gt_targets(dataset_path: Path) -> dict[str, tuple[int, int]]:
	targets: dict[str, tuple[int, int]] = {}
	with dataset_path.open("r", encoding="utf-8") as fh:
		reader = csv.DictReader(fh)
		for row in reader:
			timestamp = (row.get("timestamp") or "").strip()
			if not timestamp:
				continue
			gt_target_range = _parse_gt_target_range(row.get("GT - Target"))
			if gt_target_range is None:
				continue
			targets[timestamp] = gt_target_range
	return targets


def load_dataset_rows(dataset_path: Path) -> dict[str, dict[str, Any]]:
	rows: dict[str, dict[str, Any]] = {}
	with dataset_path.open("r", encoding="utf-8") as fh:
		reader = csv.DictReader(fh)
		for row in reader:
			timestamp = (row.get("timestamp") or "").strip()
			if not timestamp:
				continue
			rows[timestamp] = row
	return rows


def validate_dataset_gt_ranges(dataset_path: Path) -> list[dict[str, Any]]:
	mismatches: list[dict[str, Any]] = []
	with dataset_path.open("r", encoding="utf-8") as fh:
		reader = csv.DictReader(fh)
		for row in reader:
			timestamp = (row.get("timestamp") or "").strip()
			if not timestamp:
				continue
			expected_low, expected_high = _compute_expected_gt_range(row)
			expected_range = f"{expected_low}-{expected_high}"
			actual_range = (row.get("GT - Target") or "").strip()
			if actual_range != expected_range:
				mismatches.append(
					{
						"timestamp": timestamp,
						"actual_gt_range": actual_range,
						"expected_gt_range": expected_range,
						"ambient_light_lux": row.get("ambient_light_lux"),
						"occupancy_count": row.get("occupancy_count"),
						"motion_detected": row.get("motion_detected"),
						"hour": row.get("hour"),
					}
				)
	return mismatches


def compare_log_with_csv(
	log_path: Path,
	dataset_path: Path,
	use_timefilter: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
	gt_targets = load_gt_targets(dataset_path)
	dataset_rows = load_dataset_rows(dataset_path)
	comparisons: list[dict[str, Any]] = []

	last_dataset_timestamp = STARTDATASETTIMESTAMP if use_timefilter else ""
	pending_by_uuid: dict[str, int] = {}

	with log_path.open("r", encoding="utf-8") as fh:
		for doc in yaml.safe_load_all(fh):
			event = _to_event(doc)
			if event is None:
				continue

			concept_name = event.get("concept:name")
			if not isinstance(concept_name, str):
				continue

			cpee_transition = event.get("cpee:lifecycle:transition")
			activity_uuid = event.get("cpee:activity_uuid")

			if concept_name == SENSORCALLLABEL and cpee_transition == "activity/receiving":
				ts = _extract_dataset_timestamp_from_getsensor(event)
				if ts:
					if not use_timefilter or ts >= STARTDATASETTIMESTAMP:
						last_dataset_timestamp = ts
				continue

			if concept_name not in (LUMENCHANGELABEL, LUMENZEROINGLABEL):
				continue

			if cpee_transition == "activity/calling":
				lumen_value = _extract_lumen_from_call(event)
				if lumen_value is None and concept_name == LUMENZEROINGLABEL:
					lumen_value = 0

				if isinstance(activity_uuid, str) and lumen_value is None:
					pending_by_uuid[activity_uuid] = len(comparisons)

				gt_range = gt_targets.get(last_dataset_timestamp)
				gt_min = gt_range[0] if gt_range is not None else None
				gt_max = gt_range[1] if gt_range is not None else None
				dataset_row = dataset_rows.get(last_dataset_timestamp, {})
				comparisons.append(
					{
						"concept:name": concept_name,
						"activity_uuid": activity_uuid if isinstance(activity_uuid, str) else "",
						"event_time": event.get("time:timestamp") if isinstance(event.get("time:timestamp"), str) else "",
						"dataset_timestamp": last_dataset_timestamp,
						"occupancy_count": dataset_row.get("occupancy_count"),
						"ambient_light_lux": dataset_row.get("ambient_light_lux"),
						"current_light_lumen": dataset_row.get("Current Light Lumen"),
						"lumen_sent": lumen_value,
						"gt_target_min": gt_min,
						"gt_target_max": gt_max,
						"gt_target": f"{gt_min}-{gt_max}" if gt_range is not None else None,
						"match": (
							_in_gt_target_range(lumen_value, gt_range)
							if (lumen_value is not None and gt_range is not None)
							else None
						),
						"lumen_source": "calling-data" if lumen_value is not None else "unknown",
					}
				)
				continue

			if cpee_transition == "activity/receiving" and isinstance(activity_uuid, str):
				index = pending_by_uuid.get(activity_uuid)
				if index is None:
					continue
				lumen_value = _extract_applied_lumen_from_receive(event)
				if lumen_value is None:
					continue
				comparisons[index]["lumen_sent"] = lumen_value
				gt_min = comparisons[index].get("gt_target_min")
				gt_max = comparisons[index].get("gt_target_max")
				if isinstance(gt_min, int) and isinstance(gt_max, int):
					comparisons[index]["match"] = _in_gt_target_range(lumen_value, (gt_min, gt_max))
				else:
					comparisons[index]["match"] = None
				comparisons[index]["lumen_source"] = "receiving-result"
				pending_by_uuid.pop(activity_uuid, None)

	return comparisons, validate_dataset_gt_ranges(dataset_path)


def print_summary(rows: list[dict[str, Any]], rule_mismatches: list[dict[str, Any]]) -> None:
	comparable = [
		r
		for r in rows
		if r.get("lumen_sent") is not None and r.get("gt_target_min") is not None and r.get("gt_target_max") is not None
	]
	matches = [r for r in comparable if r.get("match") is True]
	mismatches = [r for r in comparable if r.get("match") is False]
	missing_lumen = [r for r in rows if r.get("lumen_sent") is None]
	missing_gt = [r for r in rows if r.get("gt_target") is None]

	print(f"Total lumen events: {len(rows)}")
	print(f"Comparable events: {len(comparable)}")
	print(f"Matches: {len(matches)}")
	print(f"Mismatches: {len(mismatches)}")
	print(f"Missing lumen value: {len(missing_lumen)}")
	print(f"Missing GT target: {len(missing_gt)}")
	print(f"Rule mismatches: {len(rule_mismatches)}")

	if mismatches:
		print("\nFirst 20 mismatches:")
		for row in mismatches[:20]:
			print(
				f"- {row['event_time']} | {row['concept:name']} | "
				f"dataset={row['dataset_timestamp']} | lumen={row['lumen_sent']} | gt_range={row['gt_target']}"
			)

	if rule_mismatches:
		print("\nFirst 20 GT rule mismatches:")
		for row in rule_mismatches[:20]:
			print(
				f"- {row['timestamp']} | ambient={row['ambient_light_lux']} | occupancy={row['occupancy_count']} | "
				f"motion={row['motion_detected']} | hour={row['hour']} | actual={row['actual_gt_range']} | expected={row['expected_gt_range']}"
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
		"lumen_source",
	]
	with report_path.open("w", encoding="utf-8", newline="") as fh:
		writer = csv.DictWriter(fh, fieldnames=fieldnames)
		writer.writeheader()
		for row in rows:
			writer.writerow({k: row.get(k) for k in fieldnames})


def write_rule_report(rule_mismatches: list[dict[str, Any]], report_path: Path) -> None:
	fieldnames = [
		"timestamp",
		"ambient_light_lux",
		"occupancy_count",
		"motion_detected",
		"hour",
		"actual_gt_range",
		"expected_gt_range",
	]
	with report_path.open("w", encoding="utf-8", newline="") as fh:
		writer = csv.DictWriter(fh, fieldnames=fieldnames)
		writer.writeheader()
		for row in rule_mismatches:
			writer.writerow({k: row.get(k) for k in fieldnames})


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Compare lumen change events from XES-YAML logs with dataset GT - Target values"
	)
	parser.add_argument("log", type=Path, help="Path to filtered log (.xes.yaml)")
	parser.add_argument("dataset", type=Path, help="Path to artificial_week_sensor_dataset.csv")
	parser.add_argument(
		"--report",
		type=Path,
		default=None,
		help="Optional path to write detailed CSV report",
	)
	parser.add_argument(
		"--timefilter",
		action="store_true",
		help=f"Apply STARTDATASETTIMESTAMP filter ({STARTDATASETTIMESTAMP}) to sensor timestamps",
	)
	args = parser.parse_args()

	rows, rule_mismatches = compare_log_with_csv(
		args.log,
		args.dataset,
		use_timefilter=args.timefilter,
	)
	print_summary(rows, rule_mismatches)

	if args.report is not None:
		write_report(rows, args.report)
		print(f"\nWrote report to {args.report}")
		if rule_mismatches:
			rule_report_path = args.report.with_name(f"{args.report.stem}.rule_mismatches.csv")
			write_rule_report(rule_mismatches, rule_report_path)
			print(f"Wrote rule report to {rule_report_path}")


if __name__ == "__main__":
	main()