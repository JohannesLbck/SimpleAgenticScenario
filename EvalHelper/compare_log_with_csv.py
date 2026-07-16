from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any

import yaml


SENSORCALLLABEL = "GetSensor"
#SENSORCALLLABEL = "environment_status"
TIMEOUTLABEL = "wait for next iteration"
#TIMEOUTLABEL = "sleep"
LUMENCHANGELABEL = "Change Lumens"
#LUMENCHANGELABEL = "change_lumen"
LUMENZEROINGLABEL = "Set lumen to 0"
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


def _event_timestamp_text(value: Any) -> str:
	if isinstance(value, datetime):
		return value.isoformat(sep=" ")
	return value if isinstance(value, str) else ""


def _parse_timestamp(value: str) -> datetime:
	s = value.strip()
	if not s:
		raise ValueError("timestamp is empty")

	# Support Unix timestamps directly via datetime.
	try:
		return datetime.fromtimestamp(float(s)).replace(tzinfo=None)
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
		frac_digits = []
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


def _timestamp_on_or_after(value: str, start: str) -> bool:
	try:
		return _parse_timestamp(value) >= _parse_timestamp(start)
	except ValueError:
		return value >= start


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
		# Mirror eval_sensor_log filtering style: only filter when parseable.
		return True

	if start_timestamp is not None and event_time < start_timestamp:
		return False
	if end_timestamp is not None and event_time > end_timestamp:
		return False
	return True


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
	current_light_lumen = _parse_float(row.get("Current Light Lumen", 0.0))
	base_ambient_lux = max(0.0, ambient_lux - (current_light_lumen / LUMEN_PER_LUX))
	occupancy = _parse_int(row.get("occupancy_count", 0)) or 0
	movement = _parse_bool(row.get("motion_detected", False))
	hour = _parse_float(row.get("hour", 0.0))
	target_low_lux, target_high_lux = _time_of_day_target(hour, occupancy, movement)
	return _lux_to_lumen_range(target_low_lux, target_high_lux, base_ambient_lux)


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


def _time_sensitive_false_negatives(
	gt_targets: dict[str, tuple[int, int]],
	observed_sensor_timestamps: set[str],
	observed_change_timestamps: set[str],
	start_dataset_timestamp: str | None,
	end_dataset_timestamp: str | None,
) -> list[dict[str, Any]]:
	if not gt_targets:
		return []

	if start_dataset_timestamp is None or end_dataset_timestamp is None:
		return []

	try:
		start_dt = _parse_timestamp(start_dataset_timestamp)
		end_dt = _parse_timestamp(end_dataset_timestamp)
	except ValueError:
		return []

	if end_dt < start_dt:
		start_dt, end_dt = end_dt, start_dt

	missing_rows: list[dict[str, Any]] = []
	for dataset_timestamp, gt_range in sorted(
		gt_targets.items(),
		key=lambda item: _parse_timestamp(item[0]),
	):
		try:
			dataset_dt = _parse_timestamp(dataset_timestamp)
		except ValueError:
			continue
		if dataset_dt < start_dt or dataset_dt > end_dt:
			continue

		gt_min, gt_max = gt_range
		if gt_min <= 0:
			continue

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
				"gt_target": f"{gt_min}-{gt_max}",
				"gt_target_min": gt_min,
				"gt_target_max": gt_max,
				"reason": reason,
			}
		)

	return missing_rows


def compare_log_with_csv(
	log_path: Path,
	dataset_path: Path,
	start_timestamp: datetime | None = None,
	end_timestamp: datetime | None = None,
	time_sensitive: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[float]]:
	gt_targets = load_gt_targets(dataset_path)
	dataset_rows = load_dataset_rows(dataset_path)
	comparisons: list[dict[str, Any]] = []
	false_negatives: list[dict[str, Any]] = []
	reactivity_seconds: list[float] = []

	last_timestamp = ""
	pending_by_uuid: dict[str, int] = {}
	pending_required_change: dict[str, Any] | None = None
	pending_sensor_time: str = ""
	observed_sensor_timestamps: set[str] = set()
	observed_change_timestamps: set[str] = set()
	dataset_timeline: list[str] = []

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
			event_time_str = _event_timestamp_text(event.get("time:timestamp"))
			if not _event_in_time_range(event_time_str, start_timestamp, end_timestamp):
				continue

			if concept_name == SENSORCALLLABEL and cpee_transition in {"activity/receiving", "activity/complete"}:
				ts = _extract_dataset_timestamp_from_getsensor(event)
				if ts:
					if pending_required_change is not None:
						false_negatives.append(pending_required_change)
						pending_required_change = None
					last_timestamp = ts
					observed_sensor_timestamps.add(ts)
					dataset_timeline.append(ts)
					pending_sensor_time = event_time_str
					gt_range = gt_targets.get(last_timestamp)
					if gt_range is not None and gt_range[0] > 0:
						pending_required_change = {
							"event_time": event_time_str,
							"dataset_timestamp": last_timestamp,
							"gt_target": f"{gt_range[0]}-{gt_range[1]}",
							"gt_target_min": gt_range[0],
							"gt_target_max": gt_range[1],
							"reason": "missing_following_change_lumens",
						}
				continue

			if concept_name not in (LUMENCHANGELABEL, LUMENZEROINGLABEL):
				continue

			if cpee_transition == "activity/calling":
				if pending_required_change is not None:
					pending_required_change = None
				reactivity_value: float | None = None
				if pending_sensor_time and event_time_str:
					try:
						delta_seconds = (_parse_timestamp(event_time_str) - _parse_timestamp(pending_sensor_time)).total_seconds()
						if delta_seconds >= 0:
							reactivity_value = delta_seconds
							reactivity_seconds.append(delta_seconds)
					except ValueError:
						pass
				pending_sensor_time = ""
				lumen_value = _extract_lumen_from_call(event)
				if lumen_value is None and concept_name == LUMENZEROINGLABEL:
					lumen_value = 0

				if isinstance(activity_uuid, str) and lumen_value is None:
					pending_by_uuid[activity_uuid] = len(comparisons)

				gt_range = gt_targets.get(last_timestamp)
				gt_min = gt_range[0] if gt_range is not None else None
				gt_max = gt_range[1] if gt_range is not None else None
				if last_timestamp:
					observed_change_timestamps.add(last_timestamp)
					dataset_timeline.append(last_timestamp)
				dataset_row = dataset_rows.get(last_timestamp, {})
				comparisons.append(
					{
						"concept:name": concept_name,
						"activity_uuid": activity_uuid if isinstance(activity_uuid, str) else "",
						"event_time": event_time_str,
						"dataset_timestamp": last_timestamp,
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
						"classification": (
							"true_positive"
							if (lumen_value is not None and gt_range is not None and _in_gt_target_range(lumen_value, gt_range))
							else (
								"false_positive"
								if (lumen_value is not None and gt_range is not None)
								else "unknown"
							)
						),
						"reactivity_seconds": reactivity_value,
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
					comparisons[index]["classification"] = (
						"true_positive" if comparisons[index]["match"] is True else "false_positive"
					)
				else:
					comparisons[index]["match"] = None
					comparisons[index]["classification"] = "unknown"
				comparisons[index]["lumen_source"] = "receiving-result"
				pending_by_uuid.pop(activity_uuid, None)

	if pending_required_change is not None:
		false_negatives.append(pending_required_change)

	if time_sensitive and dataset_timeline:
		false_negatives.extend(
			_time_sensitive_false_negatives(
				gt_targets=gt_targets,
				observed_sensor_timestamps=observed_sensor_timestamps,
				observed_change_timestamps=observed_change_timestamps,
				start_dataset_timestamp=dataset_timeline[0],
				end_dataset_timestamp=dataset_timeline[-1],
			)
		)
		false_negatives = _dedupe_false_negatives(false_negatives)

	return comparisons, validate_dataset_gt_ranges(dataset_path), false_negatives, reactivity_seconds


def print_summary(
	rows: list[dict[str, Any]],
	rule_mismatches: list[dict[str, Any]],
	false_negatives: list[dict[str, Any]],
	reactivity_seconds: list[float],
	start_timestamp: datetime | None,
	end_timestamp: datetime | None,
	time_sensitive: bool,
) -> None:
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

	print(f"Start timestamp filter: {start_timestamp.isoformat() if start_timestamp else 'none'}")
	print(f"End timestamp filter:   {end_timestamp.isoformat() if end_timestamp else 'none'}")
	print(f"Time-sensitive mode:    {'enabled' if time_sensitive else 'disabled'}")
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
	print(f"Rule mismatches: {len(rule_mismatches)}")

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
				f"- {row['event_time']} | {SENSORCALLLABEL} complete | "
				f"dataset={row['dataset_timestamp']} | gt_range={row['gt_target']} | reason={row['reason']}"
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
		"--from",
		dest="start",
		default=None,
		metavar="TIMESTAMP",
		help="Only include events at or after this timestamp (e.g. 2026-06-03T14:22:00)",
	)
	parser.add_argument(
		"--to",
		dest="end",
		default=None,
		metavar="TIMESTAMP",
		help="Only include events before this timestamp (e.g. 2026-06-03T14:22:00)",
	)
	parser.add_argument(
		"--time-sensitive",
		action="store_true",
		help="Count expected GT events missing from the log as false negatives",
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

	rows, rule_mismatches, false_negatives, reactivity_seconds = compare_log_with_csv(
		args.log,
		args.dataset,
		start_timestamp=start_timestamp,
		end_timestamp=end_timestamp,
		time_sensitive=args.time_sensitive,
	)
	print_summary(
		rows,
		rule_mismatches,
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
			fn_report_path = args.report.with_name(f"{args.report.stem}.false_negatives.csv")
			write_false_negative_report(false_negatives, fn_report_path)
			print(f"Wrote false negative report to {fn_report_path}")
		if rule_mismatches:
			rule_report_path = args.report.with_name(f"{args.report.stem}.rule_mismatches.csv")
			write_rule_report(rule_mismatches, rule_report_path)
			print(f"Wrote rule report to {rule_report_path}")


if __name__ == "__main__":
	main()
