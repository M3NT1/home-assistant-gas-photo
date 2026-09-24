"""Isolated Unit Tests for Ledger with Interpolation Engine integration.

Zero Home Assistant dependencies. Pure Python with Decimal exact equality.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
import sys
from pathlib import Path

pkg_dir = Path(__file__).resolve().parent.parent / "custom_components" / "gas_photo"
if str(pkg_dir) not in sys.path:
    sys.path.insert(0, str(pkg_dir))

try:
    from custom_components.gas_photo.ledger import Ledger, timestamp
    from custom_components.gas_photo.interpolation import UTC, BUDAPEST, quantize_m3
except ImportError:
    from ledger import Ledger, timestamp
    from interpolation import UTC, BUDAPEST, quantize_m3


def test_ledger_single_epoch_interpolation():
    """Verify Ledger builds interpolation engine and produces exact sum."""
    ledger = Ledger(max_m3_per_hour=6)

    # 36-hour interval: 2026-10-01 08:00 UTC to 2026-10-02 20:00 UTC
    t_l = "2026-10-01T08:00:00+00:00"
    t_r = "2026-10-02T20:00:00+00:00"
    id1 = "a" * 64
    id2 = "b" * 64

    now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)

    ledger.apply([
        {
            "id": id1,
            "meter_id": "gas_main",
            "captured_at": t_l,
            "value": "1000.000",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
        {
            "id": id2,
            "meter_id": "gas_main",
            "captured_at": t_r,
            "value": "1001.200",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
    ], now=now)

    # Check revision_id
    assert ledger.data.get("revision_id") is not None
    assert ledger.data["revision_id"] >= 2

    # Check estimated_hourly
    hours = ledger.estimated_hourly(now=now, baseline_sum=Decimal("0.000"))
    assert len(hours) == 36
    total_change = sum(Decimal(str(round(h["change"], 3))) for h in hours)
    assert total_change == Decimal("1.200")

    # Check daily coverage
    cov = ledger.daily_coverage()
    # 2026-10-01 is PARTIAL (started at 10:00 local)
    # 2026-10-02 is PARTIAL (ended at 22:00 local)
    assert cov.get("2026-10-01") == "PARTIAL"
    assert cov.get("2026-10-02") == "PARTIAL"

    # Backward compatibility: legacy hourly() still returns observation points
    legacy_hours = ledger.hourly(now=now)
    assert len(legacy_hours) == 2


def test_ledger_meter_replacement_multi_epoch():
    """Verify Ledger handles explicit meter replacement across epochs."""
    ledger = Ledger(max_m3_per_hour=6)

    t0 = "2026-10-15T08:00:00+00:00"
    t_c = "2026-10-15T11:30:00+00:00"
    t1 = "2026-10-15T14:00:00+00:00"

    id0 = "1" * 64
    id1 = "2" * 64
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)

    # Register meter replacement first
    ledger.add_meter_replacement(
        old_final_reading="9850.200",
        new_initial_reading="12.000",
        replacement_time=t_c,
    )

    # Apply readings: old meter before t_c, new meter after t_c
    ledger.apply([
        {
            "id": id0,
            "meter_id": "gas_main",
            "captured_at": t0,
            "value": "9848.200",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
        {
            "id": id1,
            "meter_id": "gas_main",
            "captured_at": t1,
            "value": "14.500",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
    ], now=now)

    # Check estimated hours: 6 whole hours: 08:00, 09:00, 10:00, 11:00, 12:00, 13:00
    hours = ledger.estimated_hourly(now=now, baseline_sum=Decimal("100.000"))
    assert len(hours) == 6

    # Delta V total: (9850.200 - 9848.200) + (14.500 - 12.000) = 2.000 + 2.500 = 4.500 m³
    total_change = sum(Decimal(str(round(h["change"], 3))) for h in hours)
    assert total_change == Decimal("4.500")

    # Sum continuity: ends at 100.000 + 4.500 = 104.500
    assert Decimal(str(round(hours[-1]["sum"], 3))) == Decimal("104.500")


def test_ledger_legacy_hourly_after_meter_replacement_no_negative_sum():
    """Bug 1 Regression Test: A régi publikáció (hourly, prepare_publication) nem adhat negatív sum-ot mérőcsere után."""
    ledger = Ledger(max_m3_per_hour=6)

    t0 = "2026-10-15T08:00:00+00:00"
    t_c = "2026-10-15T11:30:00+00:00"
    t1 = "2026-10-15T14:00:00+00:00"

    id0 = "3" * 64
    id1 = "4" * 64
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)

    ledger.add_meter_replacement(
        old_final_reading="9850.200",
        new_initial_reading="12.000",
        replacement_time=t_c,
    )

    ledger.apply([
        {
            "id": id0,
            "meter_id": "gas_main",
            "captured_at": t0,
            "value": "9848.200",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
        {
            "id": id1,
            "meter_id": "gas_main",
            "captured_at": t1,
            "value": "14.500",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
    ], now=now)

    # Régi publikációs útvonal ellenőrzése
    legacy_rows = ledger.prepare_publication(now=now)
    assert len(legacy_rows) == 2

    # 1. sor (08:00): sum == 0.0
    assert legacy_rows[0]['sum'] == 0.0
    # 2. sor (14:00): sum == 4.500 (2.000 a régi mérőn + 2.500 az új mérőn)
    # Tilos a -9833.7 negatív érték előállítása!
    assert legacy_rows[1]['sum'] == 4.5, f"Negatív vagy hibás sum mérőcsere után: {legacy_rows[1]['sum']}"
    assert legacy_rows[1]['sum'] >= legacy_rows[0]['sum'], "A kumulált sum nem monoton növekvő!"


def test_ledger_meter_replacement_without_subsequent_photo():
    """Bug 2 Regression Test: A mérőcsere lezáró pontja újabb fotó nélkül is önálló végpontként publikálódik."""
    ledger = Ledger(max_m3_per_hour=6)

    t0 = "2026-10-15T08:00:00+00:00"
    t1 = "2026-10-15T09:00:00+00:00"
    t_c = "2026-10-15T11:30:00+00:00"

    id0 = "5" * 64
    id1 = "6" * 64
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)

    # Két korábbi fotó
    ledger.apply([
        {
            "id": id0,
            "meter_id": "gas_main",
            "captured_at": t0,
            "value": "9848.200",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
        {
            "id": id1,
            "meter_id": "gas_main",
            "captured_at": t1,
            "value": "9849.000",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
    ], now=now)

    # 11:30-as csere rögzítése végállással, de még NINCS új fotó az új mérőről
    ledger.add_meter_replacement(
        old_final_reading="9850.200",
        new_initial_reading="12.000",
        replacement_time=t_c,
    )

    hours = ledger.estimated_hourly(now=now, baseline_sum=Decimal("0.000"))
    # A 08:00–11:30 intervallumból az egész órák: 08:00, 09:00, 10:00 (zárul 11:00-kor)
    # Nem maradhat ki a 09:00 és 10:00 óra!
    assert len(hours) == 3, f"Elvárt 3 publikált óra, kapott: {len(hours)}"
    assert hours[0]["start"].startswith("2026-10-15T08:00:00")
    assert hours[1]["start"].startswith("2026-10-15T09:00:00")
    assert hours[2]["start"].startswith("2026-10-15T10:00:00")

    # A 3 óra összege a modell szerint:
    # 08:00-09:00: 0.800
    # 09:00-11:30: delta = 1.200 / 2.5h = 0.480 / h
    # 09:00-10:00: 0.480, 10:00-11:00: 0.480 -> összesen 1.760 m³
    total_change = sum(Decimal(str(round(h["change"], 3))) for h in hours)
    assert total_change == Decimal("1.760")


def test_ledger_duplicate_timestamps_collapse():
    """Bug 3 Regression Test: Elfogadott, azonos időpontú fotók nem omlaszthatják össze az interpolációt."""
    ledger = Ledger(max_m3_per_hour=6)

    t_same = "2026-10-01T08:00:00+00:00"
    t_later = "2026-10-01T12:00:00+00:00"
    id_a = "7" * 64
    id_b = "8" * 64
    id_c = "9" * 64

    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

    # Két külön fotó rekord azonos időponttal és azonos mérőállással
    ledger.apply([
        {
            "id": id_a,
            "meter_id": "gas_main",
            "captured_at": t_same,
            "value": "1000.000",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
        {
            "id": id_b,
            "meter_id": "gas_main",
            "captured_at": t_same,
            "value": "1000.000",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
        {
            "id": id_c,
            "meter_id": "gas_main",
            "captured_at": t_later,
            "value": "1001.000",
            "revision": 1,
            "source": "manual_review",
            "metadata": {},
        },
    ], now=now)

    # Mindhárom fotó rekord megőrződik a ledgerben:
    assert len(ledger._all()) == 3

    # Az interpolációs motornak nem szabad hibát dobni (ValueError: Left timestamp must be strictly earlier than right timestamp)
    engine = ledger.build_engine()
    assert len(engine.intervals) == 1

    # 4 óra jön létre (08:00, 09:00, 10:00, 11:00), összegük 1.000 m³
    hours = ledger.estimated_hourly(now=now)
    assert len(hours) == 4
    total_change = sum(Decimal(str(round(h["change"], 3))) for h in hours)
    assert total_change == Decimal("1.000")


def test_ledger_correction_auto_revision_increment():
    """Ledger automatikus revízió inkrementálás ellenőrzése korrekció esetén."""
    ledger = Ledger(max_m3_per_hour=6)
    t = "2026-10-01T08:00:00+00:00"
    rec_id = "e" * 64
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

    ledger.apply([{
        "id": rec_id,
        "meter_id": "gas_main",
        "captured_at": t,
        "value": "1000.000",
        "revision": 1,
        "source": "manual_review",
        "metadata": {},
    }], now=now)
    rev1 = ledger.data["revision_id"]

    # Utólagos korrekció ugyanarra az id-ra nagyobb revízióval
    ledger.apply([{
        "id": rec_id,
        "meter_id": "gas_main",
        "captured_at": t,
        "value": "1000.500",
        "revision": 2,
        "source": "manual_review",
        "metadata": {},
    }], now=now)
    rev2 = ledger.data["revision_id"]

    assert rev2 > rev1, f"A ledger revision_id-nak automatikusan növekednie kell! (rev1={rev1}, rev2={rev2})"
