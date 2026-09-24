"""Level 1 Isolated Unit Tests for Gas Meter Interpolation Engine.

Implements all 10 test cases from the v8.1 Specification (UT-01 through UT-10).
Zero Home Assistant dependencies. Pure Python with Decimal exact equality.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from zoneinfo import ZoneInfo

import sys
from pathlib import Path

# Support running isolated unit tests without Home Assistant dependencies
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


def test_ut01_normal_36h_split():
    """UT-01: Normál 36 órás lefedés felosztása.
    
    t_L: 2026-10-01 08:00 UTC, t_R: 2026-10-02 20:00 UTC, Delta V = 1.200 m³.
    Követelmény: pontosan 36 diszkrét órás rekord jön létre, és
    összegük szigorúan == Decimal('1.200').
    """
    t_l = datetime(2026, 10, 1, 8, 0, 0, tzinfo=UTC)
    t_r = datetime(2026, 10, 2, 20, 0, 0, tzinfo=UTC)
    delta_v = Decimal("1.200")

    p1 = ReadingPoint(captured_at=t_l, reading=Decimal("1000.000"), epoch_id=1)
    p2 = ReadingPoint(captured_at=t_r, reading=Decimal("1000.000") + delta_v, epoch_id=1)
    interval = MeasurementInterval(left=p1, right=p2, epoch_id=1)

    engine = InterpolationEngine(target_tz=BUDAPEST)
    engine.set_intervals([interval])

    records = engine.get_published_hours(baseline_sum=Decimal("0.000"))

    assert len(records) == 36, f"Elvárt 36 óra, kapott: {len(records)}"
    total_change = sum(r.change for r in records)
    assert total_change == delta_v, f"Összegeltérés! Elvárt: {delta_v}, kapott: {total_change}"
    assert records[-1].cumulative_sum == delta_v


def test_ut02_deterministic_zero_residual():
    """UT-02: Determinisztikus maradéktalan összeg.
    
    Delta V = 1.200 m³, 36 vödör.
    Követelmény: nincs float maradék (1.1999...), minden órás vödör 0,001 m³
    felbontású Decimal érték, szigorú egyenlőség teljesül.
    """
    t_l = datetime(2026, 10, 1, 8, 0, 0, tzinfo=UTC)
    t_r = datetime(2026, 10, 2, 20, 0, 0, tzinfo=UTC)
    delta_v = Decimal("1.200")

    p1 = ReadingPoint(captured_at=t_l, reading=Decimal("1000.000"), epoch_id=1)
    p2 = ReadingPoint(captured_at=t_r, reading=Decimal("1000.000") + delta_v, epoch_id=1)
    interval = MeasurementInterval(left=p1, right=p2, epoch_id=1)

    engine = InterpolationEngine()
    engine.set_intervals([interval])

    records = engine.get_published_hours()

    for r in records:
        assert isinstance(r.change, Decimal)
        # Szigorú 0.001 felbontás (exponent == -3)
        assert r.change.as_tuple().exponent == -3, f"Nem 0.001 felbontású: {r.change}"

    # Szigorú egyenlőség (nem lebegőpontos)
    total_sum = sum(r.change for r in records)
    assert total_sum == Decimal("1.200")
    # Bizonyítani, hogy a natív osztás maradékot hagyna, de a teleszkópos összeg nem:
    naive_sum = sum([Decimal("1.200") / 36] * 36)
    assert naive_sum != Decimal("1.200")  # Naiv osztás hibát ad
    assert total_sum == Decimal("1.200")  # A motor teleszkópos összege egzakt!


def test_ut03_fractional_hours_and_boundaries():
    """UT-03: Tört órák globális unió alapján.
    
    Fotó: 15:02:58 és másnap 18:45:00.
    Követelmény: 15:00–16:00 és 18:00–19:00 nem kerül publikálásra.
    Az első publikált óra 16:00, az utolsó 17:00–18:00.
    A publikált órák összege szigorúan egyezik S(18:00) - S(16:00)-val.
    """
    t_l = datetime(2026, 10, 1, 15, 2, 58, tzinfo=UTC)
    t_r = datetime(2026, 10, 2, 18, 45, 0, tzinfo=UTC)
    delta_v = Decimal("5.000")

    p1 = ReadingPoint(captured_at=t_l, reading=Decimal("2000.000"), epoch_id=1)
    p2 = ReadingPoint(captured_at=t_r, reading=Decimal("2000.000") + delta_v, epoch_id=1)
    interval = MeasurementInterval(left=p1, right=p2, epoch_id=1)

    engine = InterpolationEngine()
    engine.set_intervals([interval])

    records = engine.get_published_hours()

    # Első publikált óra: 16:00
    assert records[0].start_utc == datetime(2026, 10, 1, 16, 0, 0, tzinfo=UTC)
    # Utolsó publikált óra: 17:00 (ami 18:00-kor zárul)
    assert records[-1].start_utc == datetime(2026, 10, 2, 17, 0, 0, tzinfo=UTC)

    # 16:00 és 18:00 között 26 óra van
    assert len(records) == 26

    # A publikált órák összege egzaktul S(18:00:00) - S(16:00:00)
    expected_published_sum = interval.cumulative_at(datetime(2026, 10, 2, 18, 0, 0, tzinfo=UTC)) - \
                             interval.cumulative_at(datetime(2026, 10, 1, 16, 0, 0, tzinfo=UTC))
    assert sum(r.change for r in records) == expected_published_sum


def test_ut04_interval_union_coverage():
    """UT-04: Intervallum-unió lefedettség.
    
    1. fotó 10:00 local (08:00 UTC), 2. fotó másnap 08:00 local (06:00 UTC).
    1. nap: PARTIAL, 2. nap: PARTIAL.
    Ha a fotók lefedik a teljes éjféltől-éjfélig tartó időszakot: CLOSED.
    Kívül eső nap: OPEN.
    """
    # 2026-10-01 nyári időszámítás (CEST, UTC+2)
    # 10:00 local = 08:00 UTC
    t1 = datetime(2026, 10, 1, 8, 0, 0, tzinfo=UTC)
    # Másnap 08:00 local = 06:00 UTC
    t2 = datetime(2026, 10, 2, 6, 0, 0, tzinfo=UTC)

    p1 = ReadingPoint(captured_at=t1, reading=Decimal("100.000"), epoch_id=1)
    p2 = ReadingPoint(captured_at=t2, reading=Decimal("102.000"), epoch_id=1)
    interval = MeasurementInterval(left=p1, right=p2, epoch_id=1)

    engine = InterpolationEngine(target_tz=BUDAPEST)
    engine.set_intervals([interval])

    st1, ratio1 = engine.evaluate_day_coverage(date(2026, 10, 1))
    st2, ratio2 = engine.evaluate_day_coverage(date(2026, 10, 2))
    st3, ratio3 = engine.evaluate_day_coverage(date(2026, 10, 5))

    assert st1 == "PARTIAL"
    assert ratio1 < Decimal("1.000") and ratio1 > Decimal("0.000")

    assert st2 == "PARTIAL"
    assert ratio2 < Decimal("1.000") and ratio2 > Decimal("0.000")

    assert st3 == "OPEN"
    assert ratio3 == Decimal("0.000")

    # Bővítjük a mérést úgy, hogy 2026-10-01 teljes egészében fedett legyen:
    # 2026-09-30 23:00 local (21:00 UTC) -> 2026-10-02 02:00 local (00:00 UTC)
    p_full_left = ReadingPoint(captured_at=datetime(2026, 9, 30, 21, 0, 0, tzinfo=UTC), reading=Decimal("95.000"), epoch_id=1)
    p_full_right = ReadingPoint(captured_at=datetime(2026, 10, 2, 0, 0, 0, tzinfo=UTC), reading=Decimal("105.000"), epoch_id=1)
    engine.set_intervals([MeasurementInterval(p_full_left, p_full_right, epoch_id=1)])

    st_full, ratio_full = engine.evaluate_day_coverage(date(2026, 10, 1))
    assert st_full == "CLOSED"
    assert ratio_full == Decimal("1.000")


def test_ut05_dst_transitions():
    """UT-05: Téli/nyári óraátállítás (DST).
    
    Őszi 25 órás és tavaszi 23 órás napok.
    Időtartam-számítás kizárólag UTC-ben, naptári határok Budapest szerint.
    Követelmény: ősszel 25 óra, tavasszal 23 óra, sum bucket_delta == Delta V szigorúan.
    """
    engine = InterpolationEngine(target_tz=BUDAPEST)

    # 1. Őszi óraátállítás: 2026-10-25 (03:00-kor visszaáll 02:00-ra, 25 órás nap)
    # Helyi éjfél: 2026-10-25 00:00 CEST = 2026-10-24 22:00 UTC
    # Helyi éjfél másnap: 2026-10-26 00:00 CET = 2026-10-25 23:00 UTC (25 óra!)
    t_autumn_start = datetime(2026, 10, 24, 22, 0, 0, tzinfo=UTC)
    t_autumn_end = datetime(2026, 10, 25, 23, 0, 0, tzinfo=UTC)
    delta_autumn = Decimal("2.500")

    p_a1 = ReadingPoint(captured_at=t_autumn_start, reading=Decimal("1000.000"), epoch_id=1)
    p_a2 = ReadingPoint(captured_at=t_autumn_end, reading=Decimal("1000.000") + delta_autumn, epoch_id=1)
    engine.set_intervals([MeasurementInterval(p_a1, p_a2, epoch_id=1)])

    cov_a, _ = engine.evaluate_day_coverage(date(2026, 10, 25))
    assert cov_a == "CLOSED"

    records_a = engine.get_published_hours()
    assert len(records_a) == 25, f"Őszi DST napon 25 órának kell lennie, kapott: {len(records_a)}"
    assert sum(r.change for r in records_a) == delta_autumn

    # 2. Tavaszi óraátállítás: 2026-03-29 (02:00-kor előreugrik 03:00-ra, 23 órás nap)
    # Helyi éjfél: 2026-03-29 00:00 CET = 2026-03-28 23:00 UTC
    # Helyi éjfél másnap: 2026-03-30 00:00 CEST = 2026-03-29 22:00 UTC (23 óra!)
    t_spring_start = datetime(2026, 3, 28, 23, 0, 0, tzinfo=UTC)
    t_spring_end = datetime(2026, 3, 29, 22, 0, 0, tzinfo=UTC)
    delta_spring = Decimal("2.300")

    p_s1 = ReadingPoint(captured_at=t_spring_start, reading=Decimal("2000.000"), epoch_id=1)
    p_s2 = ReadingPoint(captured_at=t_spring_end, reading=Decimal("2000.000") + delta_spring, epoch_id=1)
    engine.set_intervals([MeasurementInterval(p_s1, p_s2, epoch_id=1)])

    cov_s, _ = engine.evaluate_day_coverage(date(2026, 3, 29))
    assert cov_s == "CLOSED"

    records_s = engine.get_published_hours()
    assert len(records_s) == 23, f"Tavaszi DST napon 23 órának kell lennie, kapott: {len(records_s)}"
    assert sum(r.change for r in records_s) == delta_spring


def test_ut06_zero_consumption():
    """UT-06: Nulla fogyasztás lefedettségtől független kezelése.
    
    V_L = 1900.000, V_R = 1900.000 Hétfő 10:00–14:00.
    Követelmény: minden lefedett óra 0.000 m³, a lefedettség PARTIAL
    (a nulla fogyasztás nem teszi a napot CLOSED-dá).
    """
    # 2026-10-05 10:00 local (08:00 UTC) -> 14:00 local (12:00 UTC)
    t_l = datetime(2026, 10, 5, 8, 0, 0, tzinfo=UTC)
    t_r = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)

    p1 = ReadingPoint(captured_at=t_l, reading=Decimal("1900.000"), epoch_id=1)
    p2 = ReadingPoint(captured_at=t_r, reading=Decimal("1900.000"), epoch_id=1)
    interval = MeasurementInterval(left=p1, right=p2, epoch_id=1)

    engine = InterpolationEngine(target_tz=BUDAPEST)
    engine.set_intervals([interval])

    cov, ratio = engine.evaluate_day_coverage(date(2026, 10, 5))
    assert cov == "PARTIAL", "A 0 fogyasztás nem teheti a részleges napot CLOSED-dá!"
    assert ratio == Decimal("0.167")  # 4 óra / 24 óra

    records = engine.get_published_hours()
    assert len(records) == 4
    for r in records:
        assert r.change == Decimal("0.000")
    assert sum(r.change for r in records) == Decimal("0.000")


def test_ut07_intermediate_reading_insertion():
    """UT-07: Időben visszamenőleges beszúrás.
    
    Meglévő: 10-01 08:00 és 10-03 08:00 (Delta V = 4.800).
    Új közbenső fotó: 10-02 12:00 (Delta V1 = 2.000, Delta V2 = 2.800).
    Követelmény: 48 órás intervallum két 28 és 20 órás szakaszra bomlik,
    10-02 lefedettsége CLOSED marad, a teljes 48 óra összege pontosan 4.800 m³.
    """
    t1 = datetime(2026, 10, 1, 8, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 3, 8, 0, 0, tzinfo=UTC)
    t_mid = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

    p1 = ReadingPoint(captured_at=t1, reading=Decimal("1000.000"), epoch_id=1)
    p2 = ReadingPoint(captured_at=t2, reading=Decimal("1004.800"), epoch_id=1)
    p_mid = ReadingPoint(captured_at=t_mid, reading=Decimal("1002.000"), epoch_id=1)

    engine = InterpolationEngine(target_tz=BUDAPEST)

    # 1. Állapot a beszúrás előtt
    engine.set_intervals([MeasurementInterval(p1, p2, epoch_id=1)])
    cov_pre, _ = engine.evaluate_day_coverage(date(2026, 10, 2))
    assert cov_pre == "CLOSED"
    assert sum(r.change for r in engine.get_published_hours()) == Decimal("4.800")

    # 2. Állapot a közbenső fotó beszúrása után
    iv1 = MeasurementInterval(p1, p_mid, epoch_id=1)
    iv2 = MeasurementInterval(p_mid, p2, epoch_id=1)
    engine.set_intervals([iv1, iv2])

    cov_post, _ = engine.evaluate_day_coverage(date(2026, 10, 2))
    assert cov_post == "CLOSED"

    records = engine.get_published_hours()
    assert len(records) == 48
    assert sum(r.change for r in records) == Decimal("4.800")


def test_ut08a_correction_mathematics():
    """UT-08a: Korrekció számítási matematikája (Unit teszt).
    
    10-02 fotója utólag javítva: 1002.000 helyett 1001.500.
    Követelmény: revision_id növekszik, az érintett órák újraszámolódnak,
    a teljes összegmegőrzés szigorúan teljesül (4.800 m³).
    """
    t1 = datetime(2026, 10, 1, 8, 0, 0, tzinfo=UTC)
    t_mid = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 3, 8, 0, 0, tzinfo=UTC)

    p1 = ReadingPoint(captured_at=t1, reading=Decimal("1000.000"), epoch_id=1)
    p2 = ReadingPoint(captured_at=t2, reading=Decimal("1004.800"), epoch_id=1)

    # Hibás leolvasás
    p_err = ReadingPoint(captured_at=t_mid, reading=Decimal("1002.000"), epoch_id=1, revision=1)
    engine = InterpolationEngine()
    engine.set_intervals([MeasurementInterval(p1, p_err, epoch_id=1), MeasurementInterval(p_err, p2, epoch_id=1)], revision_id=1)
    records_rev1 = engine.get_published_hours()
    assert sum(r.change for r in records_rev1) == Decimal("4.800")

    # Korrekció alkalmazása (1001.500, automatikus revíziónövekedés tesztelése)
    p_corr = ReadingPoint(captured_at=t_mid, reading=Decimal("1001.500"), epoch_id=1, revision=2)
    # Nem adunk át revision_id-t expliciten: a motornak automatikusan kell növelnie
    engine.set_intervals([MeasurementInterval(p1, p_corr, epoch_id=1), MeasurementInterval(p_corr, p2, epoch_id=1)])
    records_rev2 = engine.get_published_hours()

    assert engine.revision_id == 2, f"Automatikus revíziónövekedés elvárt (2), kapott: {engine.revision_id}"
    assert len(records_rev2) == 48
    assert sum(r.change for r in records_rev2) == Decimal("4.800")
    # Az első szakasz órái csökkentek, a második szakasz órái nőttek
    assert records_rev2[10].change < records_rev1[10].change
    assert records_rev2[35].change > records_rev1[35].change


def test_ut09_meter_replacement_intra_hour_exact():
    """UT-09: Explicit mérőcsere számszerű ellenőrzése.
    
    1. epoch (régi mérő): 08:00–11:30 (9848.200 -> 9850.200, Delta V1 = 2.000 m³).
    2. epoch (új mérő): 11:30–14:00 (12.000 -> 14.500, Delta V2 = 2.500 m³).
    Követelmény: Delta V_total = 4.500 m³. A 11:00–12:00 óra a két részóra összege.
    A 6 óra összege szigorúan sum(bucket_delta) == Decimal('4.500').
    Nincs skála-összemosás. A kumulált sum szigorúan monoton növekvő.
    """
    t0 = datetime(2026, 10, 15, 8, 0, 0, tzinfo=UTC)
    t_c = datetime(2026, 10, 15, 11, 30, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 15, 14, 0, 0, tzinfo=UTC)

    # Régi mérő (Epoch 1)
    p_old_start = ReadingPoint(captured_at=t0, reading=Decimal("9848.200"), epoch_id=1)
    p_old_final = ReadingPoint(captured_at=t_c, reading=Decimal("9850.200"), epoch_id=1)
    iv1 = MeasurementInterval(left=p_old_start, right=p_old_final, epoch_id=1)

    # Új mérő (Epoch 2)
    p_new_initial = ReadingPoint(captured_at=t_c, reading=Decimal("12.000"), epoch_id=2)
    p_new_end = ReadingPoint(captured_at=t1, reading=Decimal("14.500"), epoch_id=2)
    iv2 = MeasurementInterval(left=p_new_initial, right=p_new_end, epoch_id=2)

    engine = InterpolationEngine()
    engine.set_intervals([iv1, iv2])

    records = engine.get_published_hours(baseline_sum=Decimal("100.000"))

    # Pontosan 6 óra jön létre: 08:00, 09:00, 10:00, 11:00, 12:00, 13:00
    assert len(records) == 6
    assert records[0].start_utc == datetime(2026, 10, 15, 8, 0, 0, tzinfo=UTC)
    assert records[3].start_utc == datetime(2026, 10, 15, 11, 0, 0, tzinfo=UTC)
    assert records[-1].start_utc == datetime(2026, 10, 15, 13, 0, 0, tzinfo=UTC)

    # A csere órája (11:00-12:00):
    # Régi mérő rész (11:00-11:30): 2.000 * 1800/12600 = 0.2857... -> 0.286 m³
    # Új mérő rész (11:30-12:00): 2.500 * 1800/9000 = 0.500 m³
    # Összesen = 0.286 + 0.500 = 0.786 m³
    hour_11 = records[3]
    # Közvetlen számszerű ellenőrzés a specifikációban dokumentált egzakt értékre:
    assert hour_11.change == Decimal("0.786"), f"Elvárt 0.786 m³, kapott: {hour_11.change}"

    # Szigorú összegmegőrzés a teljes összfogyasztással: 2.000 + 2.500 = 4.500 m³
    total_change = sum(r.change for r in records)
    assert total_change == Decimal("4.500")

    # Sum folytonosság
    assert records[-1].cumulative_sum == Decimal("100.000") + Decimal("4.500")
    for prev, cur in zip(records, records[1:]):
        assert cur.cumulative_sum > prev.cumulative_sum


def test_ut10_skipped_72h_coverage():
    """UT-10: Sorozatos kihagyás (72 óra pontos lefedettsége).
    
    Hétfő 08:00 és Csütörtök 08:00 közötti intervallum (Budapest idő szerint).
    Követelmény:
    Hétfő: PARTIAL (16h), Kedd: CLOSED (24h), Szerda: CLOSED (24h), Csütörtök: PARTIAL (8h).
    Összesen 72 óra sum(bucket_delta) == Delta V szigorúan.
    """
    # 2026-10-05 Hétfő 08:00 local (06:00 UTC) -> 2026-10-08 Csütörtök 08:00 local (06:00 UTC)
    t_start = datetime(2026, 10, 5, 6, 0, 0, tzinfo=UTC)
    t_end = datetime(2026, 10, 8, 6, 0, 0, tzinfo=UTC)
    delta_v = Decimal("3.600")

    p1 = ReadingPoint(captured_at=t_start, reading=Decimal("500.000"), epoch_id=1)
    p2 = ReadingPoint(captured_at=t_end, reading=Decimal("503.600"), epoch_id=1)
    interval = MeasurementInterval(left=p1, right=p2, epoch_id=1)

    engine = InterpolationEngine(target_tz=BUDAPEST)
    engine.set_intervals([interval])

    # Napi lefedettségek vizsgálata
    cov_mon, r_mon = engine.evaluate_day_coverage(date(2026, 10, 5))
    cov_tue, r_tue = engine.evaluate_day_coverage(date(2026, 10, 6))
    cov_wed, r_wed = engine.evaluate_day_coverage(date(2026, 10, 7))
    cov_thu, r_thu = engine.evaluate_day_coverage(date(2026, 10, 8))

    assert cov_mon == "PARTIAL"
    assert r_mon == quantize_m3(Decimal("16") / Decimal("24"))  # 16 / 24 = 0.667

    assert cov_tue == "CLOSED"
    assert r_tue == Decimal("1.000")

    assert cov_wed == "CLOSED"
    assert r_wed == Decimal("1.000")

    assert cov_thu == "PARTIAL"
    assert r_thu == quantize_m3(Decimal("8") / Decimal("24"))   # 8 / 24 = 0.333

    # Órás felosztás vizsgálata
    records = engine.get_published_hours()
    assert len(records) == 72, f"Elvárt 72 óra, kapott: {len(records)}"
    assert sum(r.change for r in records) == delta_v
