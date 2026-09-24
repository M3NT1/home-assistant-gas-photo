"""Deterministic interpolation, cumulative quantization, and interval-union engine.

Pure Python module with zero Home Assistant dependencies.
Implements Megvalósítási Specifikáció és Tesztszerződés (v8.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

UTC = timezone.utc
BUDAPEST = ZoneInfo("Europe/Budapest")
PRECISION = Decimal("0.001")


def parse_utc_timestamp(val: str | datetime) -> datetime:
    """Parse string or datetime to timezone-aware UTC datetime."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            raise ValueError("Naive datetime not allowed")
        return val.astimezone(UTC)
    dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        raise ValueError("Timestamp requires explicit timezone/offset")
    return dt.astimezone(UTC)


def quantize_m3(val: Decimal) -> Decimal:
    """Quantize to 0.001 m³ resolution using ROUND_HALF_UP."""
    return val.quantize(PRECISION, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ReadingPoint:
    """An authentic meter reading point."""
    captured_at: datetime
    reading: Decimal
    epoch_id: int = 1
    reading_id: Optional[str] = None
    revision: int = 1

    def __post_init__(self):
        if self.captured_at.tzinfo != UTC:
            object.__setattr__(self, "captured_at", self.captured_at.astimezone(UTC))


@dataclass
class MeasurementInterval:
    """A bounded measurement interval within a single epoch."""
    left: ReadingPoint
    right: ReadingPoint
    epoch_id: int

    def __post_init__(self):
        if self.left.epoch_id != self.right.epoch_id:
            raise ValueError("Cannot interpolate across different epochs")
        if self.left.captured_at >= self.right.captured_at:
            raise ValueError("Left timestamp must be strictly earlier than right timestamp")
        if self.right.reading < self.left.reading:
            raise ValueError("Meter reading cannot decrease within an epoch")

    @property
    def start_utc(self) -> datetime:
        return self.left.captured_at

    @property
    def end_utc(self) -> datetime:
        return self.right.captured_at

    @property
    def delta_v(self) -> Decimal:
        return quantize_m3(self.right.reading - self.left.reading)

    @property
    def duration_seconds(self) -> Decimal:
        return Decimal(str((self.end_utc - self.start_utc).total_seconds()))

    def cumulative_at(self, t: datetime) -> Decimal:
        """Telescopic Cumulative Quantization S(t)."""
        t_utc = t.astimezone(UTC)
        if t_utc <= self.start_utc:
            return Decimal("0.000")
        if t_utc >= self.end_utc:
            return self.delta_v

        delta_t_sec = Decimal(str((t_utc - self.start_utc).total_seconds()))
        ratio = delta_t_sec / self.duration_seconds
        return quantize_m3(self.delta_v * ratio)

    def slice_consumption(self, t_from: datetime, t_to: datetime) -> Decimal:
        """Consumption in sub-interval [t_from, t_to] clamped to this interval."""
        t_from_utc = t_from.astimezone(UTC)
        t_to_utc = t_to.astimezone(UTC)
        if t_from_utc >= t_to_utc:
            return Decimal("0.000")

        # Clamp to [start_utc, end_utc]
        clamped_from = max(self.start_utc, t_from_utc)
        clamped_to = min(self.end_utc, t_to_utc)
        if clamped_from >= clamped_to:
            return Decimal("0.000")

        return quantize_m3(self.cumulative_at(clamped_to) - self.cumulative_at(clamped_from))


@dataclass
class ConnectedInterval:
    """A maximal connected interval in the union of all valid intervals."""
    start_utc: datetime
    end_utc: datetime

    @property
    def duration_seconds(self) -> Decimal:
        return Decimal(str((self.end_utc - self.start_utc).total_seconds()))


class IntervalUnion:
    """Manages the global union of intervals across all epochs."""

    @staticmethod
    def compute_connected_components(intervals: List[MeasurementInterval]) -> List[ConnectedInterval]:
        """Merge overlapping or abutting intervals into disjoint maximal connected intervals."""
        if not intervals:
            return []

        # Sort by start_utc, then end_utc
        sorted_intervals = sorted(intervals, key=lambda iv: (iv.start_utc, iv.end_utc))
        merged: List[ConnectedInterval] = []

        cur_start = sorted_intervals[0].start_utc
        cur_end = sorted_intervals[0].end_utc

        for iv in sorted_intervals[1:]:
            if iv.start_utc <= cur_end:
                cur_end = max(cur_end, iv.end_utc)
            else:
                merged.append(ConnectedInterval(cur_start, cur_end))
                cur_start = iv.start_utc
                cur_end = iv.end_utc
        merged.append(ConnectedInterval(cur_start, cur_end))
        return merged


@dataclass
class HourlyRecord:
    """A discrete hourly statistic record formatted for Recorder LTS."""
    start_utc: datetime
    change: Decimal
    cumulative_sum: Decimal

    @property
    def start_iso(self) -> str:
        return self.start_utc.isoformat()


class InterpolationEngine:
    """Core deterministic calculation engine implementing v8.1 specification."""

    def __init__(self, target_tz: ZoneInfo = BUDAPEST):
        self.target_tz = target_tz
        self.intervals: List[MeasurementInterval] = []
        self.revision_id: int = 1

    def set_intervals(self, intervals: List[MeasurementInterval], revision_id: Optional[int] = None):
        self.intervals = intervals
        if revision_id is not None:
            self.revision_id = revision_id
        else:
            self.revision_id += 1

    def get_connected_components(self) -> List[ConnectedInterval]:
        return IntervalUnion.compute_connected_components(self.intervals)

    def evaluate_day_coverage(self, local_date: date) -> Tuple[str, Decimal]:
        """Evaluate coverage state ('CLOSED', 'PARTIAL', 'OPEN') and coverage ratio for a local day."""
        # Midnight to midnight in local timezone, represented in UTC
        d_start_local = datetime(local_date.year, local_date.month, local_date.day, 0, 0, 0, tzinfo=self.target_tz)
        d_start_utc = d_start_local.astimezone(UTC)

        date_next = local_date + timedelta(days=1)
        d_end_local = datetime(date_next.year, date_next.month, date_next.day, 0, 0, 0, tzinfo=self.target_tz)
        d_end_utc = d_end_local.astimezone(UTC)

        total_day_seconds = Decimal(str((d_end_utc - d_start_utc).total_seconds()))
        connected = self.get_connected_components()

        overlap_seconds = Decimal("0")
        for comp in connected:
            a = max(comp.start_utc, d_start_utc)
            b = min(comp.end_utc, d_end_utc)
            if b > a:
                overlap_seconds += Decimal(str((b - a).total_seconds()))

        if overlap_seconds == 0:
            return "OPEN", Decimal("0.000")
        if overlap_seconds == total_day_seconds:
            return "CLOSED", Decimal("1.000")

        ratio = (overlap_seconds / total_day_seconds).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        return "PARTIAL", ratio

    def get_published_hours(self, baseline_sum: Decimal = Decimal("0.000")) -> List[HourlyRecord]:
        """Compute publishable hourly LTS records using the Two-Sided Closed Hours Rule."""
        connected_components = self.get_connected_components()
        records: List[HourlyRecord] = []
        cur_sum = quantize_m3(baseline_sum)

        for comp in connected_components:
            # If component duration < 3600s, publication is empty
            if (comp.end_utc - comp.start_utc).total_seconds() < 3600:
                continue

            # Ceil start to top of hour
            if (comp.start_utc.minute == 0 and comp.start_utc.second == 0 and comp.start_utc.microsecond == 0):
                pub_start = comp.start_utc
            else:
                pub_start = comp.start_utc.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

            # Floor end to top of hour
            pub_end = comp.end_utc.replace(minute=0, second=0, microsecond=0)

            if pub_start >= pub_end:
                continue

            # Iterate over whole hours in UTC
            h = pub_start
            while h < pub_end:
                h_next = h + timedelta(hours=1)

                # Aggregate slices across all intervals intersecting this hour
                h_consumption = Decimal("0.000")
                for iv in self.intervals:
                    h_consumption += iv.slice_consumption(h, h_next)

                h_consumption = quantize_m3(h_consumption)
                cur_sum = quantize_m3(cur_sum + h_consumption)
                records.append(HourlyRecord(
                    start_utc=h,
                    change=h_consumption,
                    cumulative_sum=cur_sum
                ))
                h = h_next

        return records

    def generate_coverage_snapshot(self, days: List[date]) -> Dict[str, str]:
        """Generate published_daily_coverage snapshot mapping ISO YYYY-MM-DD to coverage state."""
        return {d.isoformat(): self.evaluate_day_coverage(d)[0] for d in days}
