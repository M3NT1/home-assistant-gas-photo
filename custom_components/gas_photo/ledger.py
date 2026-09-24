"""Exact readings and deterministic observation-hour statistics; no HA dependency."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import re

try:
    from .interpolation import (
        BUDAPEST,
        InterpolationEngine,
        MeasurementInterval,
        ReadingPoint,
        quantize_m3,
    )
except (ImportError, ValueError):
    from interpolation import (
        BUDAPEST,
        InterpolationEngine,
        MeasurementInterval,
        ReadingPoint,
        quantize_m3,
    )

UTC = timezone.utc

def timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Invalid timestamp')
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('Invalid timestamp') from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('Timestamp requires explicit UTC offset')
    return dt.astimezone(UTC)

class Ledger:
    def __init__(self, data=None, max_m3_per_hour=6):
        self.data = deepcopy(data or {'records': {}, 'history': [], 'locked_ids': [], 'origin': None, 'pending': True})
        self.max_rate = Decimal(str(max_m3_per_hour))
        if not self.max_rate.is_finite() or self.max_rate <= 0:
            raise ValueError('Invalid maximum hourly rate')

    def dump(self):
        return deepcopy(self.data)

    def readings(self, offset=0, limit=500, start=None, end=None):
        rows=sorted(self.data['records'].values(), key=lambda r: (timestamp(r['captured_at']),r['id']))
        if start: rows=[r for r in rows if timestamp(r['captured_at']) >= timestamp(start)]
        if end: rows=[r for r in rows if timestamp(r['captured_at']) < timestamp(end)]
        return deepcopy(rows[max(0,offset):max(0,offset)+min(500,max(1,limit))])

    def _all(self):
        return sorted(self.data['records'].values(), key=lambda r: (timestamp(r['captured_at']),r['id']))

    def apply(self, readings, now=None):
        now=now or datetime.now(UTC)
        if not isinstance(readings,list) or not 1 <= len(readings) <= 100:
            raise ValueError('Batch requires 1–100 readings')
        candidate=self.dump()
        accepted=[]
        for raw in readings:
            if not isinstance(raw,dict): raise ValueError('Reading must be an object')
            row=deepcopy(raw)
            if set(row) - {'id','meter_id','captured_at','value','revision','source','metadata'}:
                raise ValueError('Unknown reading fields')
            if not isinstance(row.get('id'),str) or not re.fullmatch('[0-9a-f]{64}',row['id']): raise ValueError('Invalid photo SHA-256')
            if row.get('meter_id') != 'gas_main' or row.get('source') != 'manual_review': raise ValueError('Unsupported meter/source')
            if type(row.get('revision')) is not int or not 1 <= row['revision'] <= 2147483647: raise ValueError('Invalid revision')
            if not isinstance(row.get('value'),str) or not re.fullmatch(r'\d{1,5}(?:\.\d{1,3})?',row['value']): raise ValueError('Invalid m³ value')
            dt=timestamp(row.get('captured_at'))
            if dt > now: raise ValueError('Future capture time')
            meta=row.get('metadata',{})
            try: encoded=json.dumps(meta,allow_nan=False)
            except (ValueError,TypeError,RecursionError) as exc: raise ValueError('Invalid metadata') from exc
            if not isinstance(meta,dict) or len(encoded.encode()) > 16384: raise ValueError('Metadata exceeds 16 KiB')
            old=candidate['records'].get(row['id'])
            if old:
                if row == old:
                    accepted.append({'id':row['id'],'revision':row['revision']}); continue
                if row['revision'] <= old['revision']: raise ValueError('Stale or conflicting revision')
                if row['id'] in candidate['locked_ids'] and dt != timestamp(old['captured_at']):
                    raise ValueError('Published capture time cannot be moved; migration required')
                origin=candidate['origin']
                if origin and row['id'] == origin['id'] and (row['value'] != old['value'] or dt != timestamp(old['captured_at'])):
                    raise ValueError('Published baseline cannot change; migration required')
                candidate['history'].append(old)
            candidate['records'][row['id']]=row
            accepted.append({'id':row['id'],'revision':row['revision']})
        rows=sorted(candidate['records'].values(),key=lambda r:timestamp(r['captured_at']))
        origin=candidate['origin']
        if origin and rows and timestamp(rows[0]['captured_at']) < timestamp(origin['captured_at']):
            raise ValueError('Reading precedes published baseline; migration required')
        replacements = sorted(candidate.get('replacements', []), key=lambda r: timestamp(r['replacement_time']))
        if not replacements:
            for left,right in zip(rows,rows[1:]):
                delta=Decimal(right['value'])-Decimal(left['value'])
                seconds=Decimal(str((timestamp(right['captured_at'])-timestamp(left['captured_at'])).total_seconds()))
                if seconds == 0 and delta != 0: raise ValueError('Conflicting capture timestamp')
                if delta < 0: raise ValueError('Meter decrease requires review')
                if delta > self.max_rate*seconds/3600+Decimal('0.001'): raise ValueError('Implausible consumption rate')
        else:
            epochs_rows = []
            cur_epoch = []
            rep_idx = 0
            for r in rows:
                t_r = timestamp(r['captured_at'])
                while rep_idx < len(replacements) and t_r > timestamp(replacements[rep_idx]['replacement_time']):
                    epochs_rows.append(cur_epoch)
                    cur_epoch = []
                    rep_idx += 1
                cur_epoch.append(r)
            epochs_rows.append(cur_epoch)
            for epoch_r in epochs_rows:
                for left,right in zip(epoch_r, epoch_r[1:]):
                    delta=Decimal(right['value'])-Decimal(left['value'])
                    seconds=Decimal(str((timestamp(right['captured_at'])-timestamp(left['captured_at'])).total_seconds()))
                    if seconds == 0 and delta != 0: raise ValueError('Conflicting capture timestamp')
                    if delta < 0: raise ValueError('Meter decrease requires review')
                    if delta > self.max_rate*seconds/3600+Decimal('0.001'): raise ValueError('Implausible consumption rate')
        candidate['revision_id'] = candidate.get('revision_id', 1) + 1
        candidate['pending']=True
        self.data=candidate
        return accepted

    def add_meter_replacement(self, old_final_reading: str, new_initial_reading: str, replacement_time: str):
        candidate = self.dump()
        t_c = timestamp(replacement_time)
        reps = candidate.setdefault('replacements', [])
        rep = {
            'replacement_time': t_c.isoformat(),
            'old_final_reading': str(Decimal(old_final_reading)),
            'new_initial_reading': str(Decimal(new_initial_reading)),
            'epoch_id': len(reps) + 1,
        }
        reps.append(rep)
        candidate['revision_id'] = candidate.get('revision_id', 1) + 1
        candidate['pending'] = True
        self.data = candidate
        return rep

    def build_engine(self) -> InterpolationEngine:
        engine = InterpolationEngine(target_tz=BUDAPEST)
        rows = self._all()
        replacements = sorted(
            self.data.get('replacements', []),
            key=lambda r: timestamp(r['replacement_time'])
        )
        if not rows and not replacements:
            return engine

        epochs_points = []
        cur_points = []
        rep_idx = 0

        for r in rows:
            t_r = timestamp(r['captured_at'])
            while rep_idx < len(replacements) and t_r >= timestamp(replacements[rep_idx]['replacement_time']):
                rep = replacements[rep_idx]
                t_c = timestamp(rep['replacement_time'])
                cur_points.append(
                    ReadingPoint(
                        captured_at=t_c,
                        reading=Decimal(rep['old_final_reading']),
                        epoch_id=rep_idx + 1,
                    )
                )
                epochs_points.append(cur_points)
                cur_points = [
                    ReadingPoint(
                        captured_at=t_c,
                        reading=Decimal(rep['new_initial_reading']),
                        epoch_id=rep_idx + 2,
                    )
                ]
                rep_idx += 1
            cur_points.append(
                ReadingPoint(
                    captured_at=t_r,
                    reading=Decimal(r['value']),
                    epoch_id=rep_idx + 1,
                    revision=r.get('revision', 1),
                )
            )

        # Flush any remaining replacements after the last photo reading (Fix for Bug #2)
        while rep_idx < len(replacements):
            rep = replacements[rep_idx]
            t_c = timestamp(rep['replacement_time'])
            cur_points.append(
                ReadingPoint(
                    captured_at=t_c,
                    reading=Decimal(rep['old_final_reading']),
                    epoch_id=rep_idx + 1,
                )
            )
            epochs_points.append(cur_points)
            cur_points = [
                ReadingPoint(
                    captured_at=t_c,
                    reading=Decimal(rep['new_initial_reading']),
                    epoch_id=rep_idx + 2,
                )
            ]
            rep_idx += 1
        epochs_points.append(cur_points)

        intervals = []
        for pts in epochs_points:
            # Deduplicate/merge points with identical timestamps (Fix for Bug #3)
            deduped = []
            for p in sorted(pts, key=lambda pt: pt.captured_at):
                if not deduped:
                    deduped.append(p)
                elif p.captured_at == deduped[-1].captured_at:
                    if p.revision > deduped[-1].revision:
                        deduped[-1] = p
                elif p.captured_at > deduped[-1].captured_at:
                    deduped.append(p)
            for p1, p2 in zip(deduped, deduped[1:]):
                intervals.append(MeasurementInterval(left=p1, right=p2, epoch_id=p1.epoch_id))

        engine.set_intervals(intervals, revision_id=self.data.get('revision_id', 1))
        return engine

    def estimated_hourly(self, now=None, baseline_sum=Decimal("0.000")):
        now = now or datetime.now(UTC)
        cutoff = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        engine = self.build_engine()
        records = engine.get_published_hours(baseline_sum=baseline_sum)
        result = []
        for r in records:
            if r.start_utc < cutoff:
                result.append({
                    'start': r.start_utc.isoformat(),
                    'state': float(r.change),
                    'sum': float(r.cumulative_sum),
                    'change': float(r.change),
                })
        return result

    def daily_coverage(self, start_date=None, end_date=None):
        engine = self.build_engine()
        if not engine.intervals:
            return {}
        t_min = min(iv.left.captured_at for iv in engine.intervals).astimezone(BUDAPEST).date()
        t_max = max(iv.right.captured_at for iv in engine.intervals).astimezone(BUDAPEST).date()
        start_d = start_date or t_min
        end_d = end_date or t_max
        res = {}
        cur = start_d
        while cur <= end_d:
            status, ratio = engine.evaluate_day_coverage(cur)
            res[cur.isoformat()] = status
            cur += timedelta(days=1)
        return res

    def hourly(self, now=None):
        now = now or datetime.now(UTC)
        cutoff = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        rows = self._all()
        if not rows:
            return []

        replacements = sorted(
            self.data.get('replacements', []),
            key=lambda r: timestamp(r['replacement_time'])
        )

        buckets = {}
        for row in rows:
            hour = timestamp(row['captured_at']).replace(minute=0, second=0, microsecond=0)
            if hour < cutoff:
                buckets[hour] = row

        if not buckets:
            return []

        # Single epoch: simple subtraction from origin/first reading
        if not replacements:
            baseline = Decimal((self.data['origin'] or rows[0])['value'])
            return [
                {
                    'start': hour.isoformat(),
                    'state': float(Decimal(row['value'])),
                    'sum': float(Decimal(row['value']) - baseline),
                }
                for hour, row in sorted(buckets.items())
            ]

        # Multi-epoch: compute cumulative offset across replacements (Fix for Bug #1)
        epoch_configs = []
        cur_baseline = Decimal((self.data['origin'] or rows[0])['value'])
        cum_offset = Decimal('0.000')

        for rep in replacements:
            t_c = timestamp(rep['replacement_time'])
            v_old = Decimal(rep['old_final_reading'])
            v_new = Decimal(rep['new_initial_reading'])
            epoch_delta = v_old - cur_baseline
            epoch_configs.append({
                'until': t_c,
                'baseline': cur_baseline,
                'offset': cum_offset,
            })
            cum_offset += epoch_delta
            cur_baseline = v_new

        epoch_configs.append({
            'until': None,
            'baseline': cur_baseline,
            'offset': cum_offset,
        })

        result = []
        for hour, row in sorted(buckets.items()):
            t_row = timestamp(row['captured_at'])
            cfg = epoch_configs[-1]
            for ec in epoch_configs[:-1]:
                if t_row < ec['until']:
                    cfg = ec
                    break
            row_sum = cfg['offset'] + (Decimal(row['value']) - cfg['baseline'])
            result.append({
                'start': hour.isoformat(),
                'state': float(Decimal(row['value'])),
                'sum': float(row_sum),
            })
        return result

    def prepare_publication(self, now=None):
        rows=self.hourly(now)
        if rows:
            if not self.data['origin']: self.data['origin']=deepcopy(self._all()[0])
            cutoff=timestamp(rows[-1]['start'])
            self.data['locked_ids']=sorted(set(self.data['locked_ids']) | {r['id'] for r in self._all() if timestamp(r['captured_at']).replace(minute=0,second=0,microsecond=0) <= cutoff})
        self.data['pending']=True
        return rows
