"""Level 3 Dashboard Audit and Date Key Tests.

Implements Section 7.3 and Section 3.1 of the Specification.
Audits:
1. Budapest Timezone Date Key mapping (winter CET UTC+1 and summer CEST UTC+2 vs. naive UTC split).
2. ApexCharts data generator logic (CLOSED vs PARTIAL separation, null-filling, color assignment).
3. Independent audit of Equation 1 (Ledger Invariant) and Equation 2 (Recorder Indexing Formula).
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest
from zoneinfo import ZoneInfo

import sys
from pathlib import Path

pkg_dir = Path(__file__).resolve().parent.parent / "custom_components" / "gas_photo"
if str(pkg_dir) not in sys.path:
    sys.path.insert(0, str(pkg_dir))

try:
    from custom_components.gas_photo.interpolation import (
        BUDAPEST,
        InterpolationEngine,
        MeasurementInterval,
        ReadingPoint,
        UTC,
        quantize_m3,
    )
except ImportError:
    from interpolation import (
        BUDAPEST,
        InterpolationEngine,
        MeasurementInterval,
        ReadingPoint,
        UTC,
        quantize_m3,
    )


def format_budapest_date_key(dt_utc: datetime) -> str:
    """Python equivalent of JS: new Date(entry.start).toLocaleDateString('en-CA', { timeZone: 'Europe/Budapest' })."""
    return dt_utc.astimezone(BUDAPEST).strftime("%Y-%m-%d")


def naive_utc_split(dt_utc: datetime) -> str:
    """Python equivalent of buggy JS: entry.start.toISOString().split('T')[0]."""
    return dt_utc.strftime("%Y-%m-%d")


def test_level3_budapest_date_key_mapping():
    """Level 3 Audit: Időzónás dátumkulcs teszt téli és nyári időszámítás alatt.
    
    Bizonyítja, hogy a naiv UTC split('T')[0] 1 nappal visszacsúsztatja a helyi éjféli rekordot,
    míg a Budapest időzónára formázott dátumkulcs pontosan a helyes naptári naphoz rendeli a lefedettséget.
    """
    # 1. Nyári időszámítás (CEST, UTC+2): 2026-10-01 00:00:00 local = 2026-09-30 22:00:00 UTC
    dt_summer_midnight = datetime(2026, 9, 30, 22, 0, 0, tzinfo=UTC)

    buggy_summer_key = naive_utc_split(dt_summer_midnight)
    correct_summer_key = format_budapest_date_key(dt_summer_midnight)

    assert buggy_summer_key == "2026-09-30", "A naiv split valóban előző napi (hibás) dátumot állít elő!"
    assert correct_summer_key == "2026-10-01", "A helyes budapesti formázás pontosan 2026-10-01-et ad!"

    # 2. Téli időszámítás (CET, UTC+1): 2026-01-15 00:00:00 local = 2026-01-14 23:00:00 UTC
    dt_winter_midnight = datetime(2026, 1, 14, 23, 0, 0, tzinfo=UTC)

    buggy_winter_key = naive_utc_split(dt_winter_midnight)
    correct_winter_key = format_budapest_date_key(dt_winter_midnight)

    assert buggy_winter_key == "2026-01-14", "A naiv split télen is 1 nappal korábbi dátumot ad!"
    assert correct_winter_key == "2026-01-15", "A helyes budapesti formázás pontosan 2026-01-15-öt ad!"


def test_level3_apexcharts_series_separation():
    """Level 3 Audit: ApexCharts adatsorok elválasztása (CLOSED vs PARTIAL).
    
    Bizonyítja, hogy egy nap soha nem jelenik meg egyszerre mindkét sorozatban,
    a CLOSED napok a kék oszlopba (#1e88e5), a PARTIAL napok a narancs oszlopba (#ffa726) kerülnek,
    az OPEN napok mindkettőben null értéket kapnak.
    """
    coverage_map = {
        "2026-10-05": "PARTIAL", # Hétfő (16h fedett)
        "2026-10-06": "CLOSED",  # Kedd (24h fedett)
        "2026-10-07": "CLOSED",  # Szerda (24h fedett)
        "2026-10-08": "PARTIAL", # Csütörtök (8h fedett)
        "2026-10-09": "OPEN",    # Péntek (0h fedett)
    }

    # Statisztikai bejegyzések szimulálása
    stats_entries = [
        {"start": datetime(2026, 10, 4, 22, 0, 0, tzinfo=UTC), "change": 0.800}, # 2026-10-05 local
        {"start": datetime(2026, 10, 5, 22, 0, 0, tzinfo=UTC), "change": 1.200}, # 2026-10-06 local
        {"start": datetime(2026, 10, 6, 22, 0, 0, tzinfo=UTC), "change": 1.200}, # 2026-10-07 local
        {"start": datetime(2026, 10, 7, 22, 0, 0, tzinfo=UTC), "change": 0.400}, # 2026-10-08 local
        {"start": datetime(2026, 10, 8, 22, 0, 0, tzinfo=UTC), "change": 0.000}, # 2026-10-09 local
    ]

    # Szimuláljuk a kártya JavaScript data_generator logikáját mindkét sorozatra:
    series1_closed = [] # Kék oszlop: Lezárt becslés
    series2_partial = [] # Narancs oszlop: Részleges nap

    for entry in stats_entries:
        d_str = format_budapest_date_key(entry["start"])
        status = coverage_map.get(d_str, "OPEN")
        val = entry["change"]

        # 1. sorozat: csak ha CLOSED
        val1 = val if (status == "CLOSED" and val is not None) else None
        series1_closed.append((d_str, val1))

        # 2. sorozat: csak ha PARTIAL
        val2 = val if (status == "PARTIAL" and val is not None) else None
        series2_partial.append((d_str, val2))

    # Ellenőrzés:
    # 2026-10-05 (Hétfő): Series 1 == None, Series 2 == 0.800
    assert series1_closed[0] == ("2026-10-05", None)
    assert series2_partial[0] == ("2026-10-05", 0.800)

    # 2026-10-06 (Kedd): Series 1 == 1.200, Series 2 == None
    assert series1_closed[1] == ("2026-10-06", 1.200)
    assert series2_partial[1] == ("2026-10-06", None)

    # 2026-10-07 (Szerda): Series 1 == 1.200, Series 2 == None
    assert series1_closed[2] == ("2026-10-07", 1.200)
    assert series2_partial[2] == ("2026-10-07", None)

    # 2026-10-08 (Csütörtök): Series 1 == None, Series 2 == 0.400
    assert series1_closed[3] == ("2026-10-08", None)
    assert series2_partial[3] == ("2026-10-08", 0.400)

    # 2026-10-09 (Péntek): Series 1 == None, Series 2 == None
    assert series1_closed[4] == ("2026-10-09", None)
    assert series2_partial[4] == ("2026-10-09", None)

    # Invariáns: soha nincs mindkettőben számérték egyszerre (zéró duplázás)
    for (_, v1), (_, v2) in zip(series1_closed, series2_partial):
        assert not (v1 is not None and v2 is not None), "Egy nap nem jelenhet meg egyszerre mindkét sorozatban!"


def test_level3_real_node_js_execution():
    """Level 3 Audit: Valódi JavaScript futtatás Node.js alatt.
    
    Közvetlenül meghívja a tests/test_apexcharts_data_generator.js szkriptet,
    amely a Node.js beépített V8 motorjában futtatja a specifikáció 3.1 pontjában
    leírt ApexCharts data_generator kódokat és ellenőrzi az eredményeket.
    """
    import subprocess
    script_path = Path(__file__).parent / "test_apexcharts_data_generator.js"
    res = subprocess.run(["node", str(script_path)], capture_output=True, text=True)
    assert res.returncode == 0, f"Node.js execution failed: {res.stderr}"
    assert "All JavaScript data_generator tests PASSED successfully!" in res.stdout


def test_level3_independent_audit_equation1_and_equation2():
    """Level 3 Audit: 1. és 2. egyenlet független matematikai auditja.
    
    1. Egyenlet (Ledger Invariáns): sum(bucket_delta_k) == Delta V_total = sum(V_e,end - V_e,start).
    2. Egyenlet (Recorder Indexelési Egyenlet):
       sum_{h in [t_pub_start, t_pub_end)} change_h == record(t_pub_end - 3600s).sum - record_baseline.sum.
    """
    # 2-epochos mérés mérőcserével (UT-09 paraméterek)
    t0 = datetime(2026, 10, 15, 8, 0, 0, tzinfo=UTC)
    t_c = datetime(2026, 10, 15, 11, 30, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 15, 14, 0, 0, tzinfo=UTC)

    # 1. Epoch
    v1_start = Decimal("9848.200")
    v1_end = Decimal("9850.200")
    delta_v1 = v1_end - v1_start # 2.000

    # 2. Epoch
    v2_start = Decimal("12.000")
    v2_end = Decimal("14.500")
    delta_v2 = v2_end - v2_start # 2.500

    delta_v_total = delta_v1 + delta_v2 # 4.500

    iv1 = MeasurementInterval(
        left=ReadingPoint(t0, v1_start, epoch_id=1),
        right=ReadingPoint(t_c, v1_end, epoch_id=1),
        epoch_id=1,
    )
    iv2 = MeasurementInterval(
        left=ReadingPoint(t_c, v2_start, epoch_id=2),
        right=ReadingPoint(t1, v2_end, epoch_id=2),
        epoch_id=2,
    )

    engine = InterpolationEngine()
    engine.set_intervals([iv1, iv2])

    baseline_sum = Decimal("50.000")
    records = engine.get_published_hours(baseline_sum=baseline_sum)

    # 1. Egyenlet auditja:
    total_bucket_sum = sum(r.change for r in records)
    assert total_bucket_sum == delta_v_total, f"1. egyenlet megsérült: {total_bucket_sum} != {delta_v_total}"

    # 2. Egyenlet auditja:
    # A publikált tartomány: [08:00, 14:00)
    # Utolsó rekord kezdete: 13:00 (ami 14:00-kor zárul)
    last_record = records[-1]
    assert last_record.start_utc == t1 - timedelta(hours=1) # 13:00

    lhs = sum(r.change for r in records) # sum(change_h)
    rhs = last_record.cumulative_sum - baseline_sum # record(t_pub_end - 3600s).sum - record_baseline.sum
    assert lhs == rhs, f"2. egyenlet megsérült: {lhs} != {rhs}"
    assert lhs == delta_v_total
