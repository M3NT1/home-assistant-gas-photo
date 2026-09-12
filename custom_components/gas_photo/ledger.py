"""Exact readings and deterministic observation-hour statistics; no HA dependency."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import re

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
        for left,right in zip(rows,rows[1:]):
            delta=Decimal(right['value'])-Decimal(left['value'])
            seconds=Decimal(str((timestamp(right['captured_at'])-timestamp(left['captured_at'])).total_seconds()))
            if seconds == 0 and delta != 0: raise ValueError('Conflicting capture timestamp')
            if delta < 0: raise ValueError('Meter decrease requires review')
            if delta > self.max_rate*seconds/3600+Decimal('0.001'): raise ValueError('Implausible consumption rate')
        candidate['pending']=True
        self.data=candidate
        return accepted

    def hourly(self, now=None):
        now=now or datetime.now(UTC)
        cutoff=now.astimezone(UTC).replace(minute=0,second=0,microsecond=0)
        rows=self._all()
        if not rows: return []
        baseline=Decimal((self.data['origin'] or rows[0])['value'])
        buckets={}
        for row in rows:
            hour=timestamp(row['captured_at']).replace(minute=0,second=0,microsecond=0)
            if hour < cutoff: buckets[hour]=row
        return [{'start':hour.isoformat(),'state':float(Decimal(row['value'])),'sum':float(Decimal(row['value'])-baseline)} for hour,row in sorted(buckets.items())]

    def prepare_publication(self, now=None):
        rows=self.hourly(now)
        if rows:
            if not self.data['origin']: self.data['origin']=deepcopy(self._all()[0])
            cutoff=timestamp(rows[-1]['start'])
            self.data['locked_ids']=sorted(set(self.data['locked_ids']) | {r['id'] for r in self._all() if timestamp(r['captured_at']).replace(minute=0,second=0,microsecond=0) <= cutoff})
        self.data['pending']=True
        return rows
