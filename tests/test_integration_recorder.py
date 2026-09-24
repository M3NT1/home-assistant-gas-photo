"""Level 2 Isolated Integration Tests using the actual Home Assistant components.

Directly tests:
- Receiver class (custom_components.gas_photo.Receiver)
- async_setup startup flow (custom_components.gas_photo.async_setup)
- WebSocket command handler (custom_components.gas_photo.websocket_readings)
- GasPhotoPublicationStatusSensor (custom_components.gas_photo.sensor.GasPhotoPublicationStatusSensor)
- Isolated SQLite Recorder DB with HA Core PR #127120 async_clear_statistics support.

Verifies:
- IT-01: Idempotency, 0.001 m³ normalization, and gappy baseline.
- IT-02: Multi-epoch meter replacement and total sum preservation.
- IT-03: Publication trigger and snapshot synchronicity (commit failure keeps pending=True).
- IT-04: Rebuild statistics deletion failure handling and startup recovery via async_setup.
- IT-05: Real WebSocket command execution and query validation.
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import pytest

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers.storage import Store
from tests.conftest import IsolatedRecorderDB, MockServices

from custom_components.gas_photo import DOMAIN, Receiver, async_setup, websocket_readings
from custom_components.gas_photo.interpolation import BUDAPEST, UTC, quantize_m3
from custom_components.gas_photo.ledger import Ledger, timestamp
from custom_components.gas_photo.sensor import GasPhotoPublicationStatusSensor


def make_id(n: int) -> str:
    """Generates a valid 64-character lowercase hex string."""
    return f"{n:064x}"


@pytest.fixture
def ha_env(tmp_path):
    """Creates a fully wired, isolated Home Assistant test environment."""
    hass = HomeAssistant()
    db_file = tmp_path / "test_recorder.db"
    recorder_db = IsolatedRecorderDB(db_file)
    hass.recorder = recorder_db

    store = Store(hass, 1, f"{DOMAIN}.ledger")
    store.data = None

    ledger = Ledger(max_m3_per_hour=6)
    receiver = Receiver(hass, store, ledger)
    hass.data[DOMAIN] = receiver

    return hass, recorder_db, store, receiver


@pytest.mark.asyncio
async def test_it01_idempotency_normalization_and_gappy_baseline(ha_env):
    """IT-01: Idempotencia, 0,001 m³ normalizálás és hézagos sorozat baseline a valós Receiverrel."""
    hass, db, store, receiver = ha_env

    # 1. 36 órás intervallum importálása (2026-09-01 08:00 - 2026-09-02 20:00 UTC)
    t_start = "2026-09-01T08:00:00+00:00"
    t_end = "2026-09-02T20:00:00+00:00"
    readings = [
        {"id": make_id(1), "meter_id": "gas_main", "captured_at": t_start, "value": "1000.000", "revision": 1, "source": "manual_review", "metadata": {}},
        {"id": make_id(2), "meter_id": "gas_main", "captured_at": t_end, "value": "1001.200", "revision": 1, "source": "manual_review", "metadata": {}},
    ]

    call = ServiceCall(DOMAIN, "import_readings", {"readings": readings})
    res = await receiver.import_readings(call)
    assert len(res["accepted"]) == 2
    assert res["statistics_status"] == "queued"

    stat_id = "gas_photo:gas_estimated"
    assert db.count_records(stat_id) == 36

    # 2. Visszaolvasás és szigorú 0,001 m³ Decimal egyezőség vizsgálata
    readback = db.statistics_during_period(
        stat_id,
        start=datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC),
        end=datetime(2026, 9, 2, 20, 0, 0, tzinfo=UTC),
    )[stat_id]
    assert len(readback) == 36

    expected_hours = receiver.ledger.estimated_hourly()
    for exp, act in zip(expected_hours, readback):
        assert act["start"] == exp["start"]
        normalized_sum = Decimal(str(round(act["sum"], 3)))
        expected_sum = Decimal(str(round(exp["sum"], 3)))
        assert normalized_sum == expected_sum

        normalized_state = Decimal(str(round(act["state"], 3)))
        expected_state = Decimal(str(round(exp["state"], 3)))
        assert normalized_state == expected_state

    # 3. Idempotencia ellenőrzése: azonos adatok ismételt importálása nem hoz létre duplikációt
    res2 = await receiver.import_readings(call)
    assert len(res2["accepted"]) == 2
    assert len(receiver.ledger.readings()) == 2
    assert db.count_records(stat_id) == 36

    # 4. Hézagos sorozat baseline vizsgálata:
    # 24 órás kihagyás után új leolvasás érkezik (2026-09-03 20:00 - 2026-09-04 20:00)
    t_gap_end = "2026-09-04T20:00:00+00:00"
    readings_gap = [
        {"id": make_id(3), "meter_id": "gas_main", "captured_at": t_gap_end, "value": "1003.600", "revision": 1, "source": "manual_review", "metadata": {}},
    ]
    call_gap = ServiceCall(DOMAIN, "import_readings", {"readings": readings_gap})
    res_gap = await receiver.import_readings(call_gap)
    assert len(res_gap["accepted"]) == 1

    # A gap utáni 48 óra publikálódott: 36 + 48 = 84 óra
    assert db.count_records(stat_id) == 84

    readback_all = db.statistics_during_period(
        stat_id,
        start=datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC),
        end=datetime(2026, 9, 4, 20, 0, 0, tzinfo=UTC),
    )[stat_id]
    assert len(readback_all) == 84

    # A végső kumulált összegnek szigorúan 3.600 m³-nek kell lennie:
    final_record = readback_all[-1]
    assert Decimal(str(round(final_record["sum"], 3))) == Decimal("3.600")


@pytest.mark.asyncio
async def test_it02_multiepoch_meter_replacement_with_actual_receiver(ha_env):
    """IT-02: Mérőcsere és összegmegőrzés a valós Receiver és meter_replacement service-en keresztül."""
    hass, db, store, receiver = ha_env

    # Epoch 1: Régi mérő leolvasások
    readings_old = [
        {"id": make_id(101), "meter_id": "gas_main", "captured_at": "2026-09-01T08:00:00+00:00", "value": "9998.000", "revision": 1, "source": "manual_review", "metadata": {}},
        {"id": make_id(102), "meter_id": "gas_main", "captured_at": "2026-09-01T12:00:00+00:00", "value": "10000.000", "revision": 1, "source": "manual_review", "metadata": {}},
    ]
    await receiver.import_readings(ServiceCall(DOMAIN, "import_readings", {"readings": readings_old}))

    # Mérőcsere végrehajtása a receiver.meter_replacement szolgáltatáson keresztül
    # Régi mérő záróállása: 10002.000, Új mérő kezdőállása: 0.000, Csere ideje: 14:00
    swap_call = ServiceCall(DOMAIN, "meter_replacement", {
        "old_final_reading": "10002.000",
        "new_initial_reading": "0.000",
        "replacement_time": "2026-09-01T14:00:00+00:00",
    })
    swap_res = await receiver.meter_replacement(swap_call)
    assert swap_res["status"] == "accepted"

    # Epoch 2: Új mérő leolvasás
    readings_new = [
        {"id": make_id(103), "meter_id": "gas_main", "captured_at": "2026-09-01T16:00:00+00:00", "value": "2.500", "revision": 1, "source": "manual_review", "metadata": {}},
    ]
    await receiver.import_readings(ServiceCall(DOMAIN, "import_readings", {"readings": readings_new}))

    stat_id = "gas_photo:gas_estimated"
    readback = db.statistics_during_period(
        stat_id,
        start=datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC),
        end=datetime(2026, 9, 1, 16, 0, 0, tzinfo=UTC),
    )[stat_id]

    assert len(readback) == 8  # 8 óra folytonos lefedettség

    # 1. Soha nem lehet negatív fogyasztás (state >= 0)
    for r in readback:
        assert r["state"] >= 0.0, f"Negatív fogyasztás észlelve: {r}"

    # 2. Összegmegőrzés:
    # Régi mérő fogyasztása: 10002.000 - 9998.000 = 4.000 m³
    # Új mérő fogyasztása: 2.500 - 0.000 = 2.500 m³
    # Teljes összeg: 6.500 m³
    final_sum = Decimal(str(round(readback[-1]["sum"], 3)))
    assert final_sum == Decimal("6.500")

    # 3. Órás részösszegek összege pontosan megegyezik a végső kumulált összeggel:
    total_states = sum(Decimal(str(round(r["state"], 3))) for r in readback)
    assert total_states == Decimal("6.500")


@pytest.mark.asyncio
async def test_it03_publication_trigger_and_snapshot_synchronicity(ha_env):
    """IT-03: Publication Trigger és Pillanatkép Szinkronitás (valós Receiver és Sensor)."""
    hass, db, store, receiver = ha_env
    sensor = GasPhotoPublicationStatusSensor(hass)

    # 1. Kezdeti állapot
    assert sensor.native_value == "pending"
    assert sensor.extra_state_attributes["published_revision_id"] == 0

    readings = [
        {"id": make_id(201), "meter_id": "gas_main", "captured_at": "2026-09-01T08:00:00+00:00", "value": "1000.000", "revision": 1, "source": "manual_review", "metadata": {}},
        {"id": make_id(202), "meter_id": "gas_main", "captured_at": "2026-09-01T12:00:00+00:00", "value": "1001.000", "revision": 1, "source": "manual_review", "metadata": {}},
    ]

    # --- SUBTEST A: Adatbázis hiba / meghiúsult tranzakció ---
    # Szimuláljuk a felhasználó által jelzett hibát: a recorder sorba állítja a kérést,
    # de a DB írás sikertelen / nem konfirmált (commit_failure = True).
    db.commit_failure = True

    call = ServiceCall(DOMAIN, "import_readings", {"readings": readings})
    res = await receiver.import_readings(call)

    # A publikáció nem tudott lezárulni, státusza "pending" maradt:
    assert res["statistics_status"] == "pending"

    # A Receiver és a Store belső állapotában a pending szigorúan TRUE:
    assert receiver.ledger.data["pending"] is True
    # A published_revision_id NEM léphetett előre a commit megerősítése nélkül!
    assert receiver.ledger.data.get("published_revision_id", 0) == 0

    # A szenzor is a korábbi érvényes állapotot mutatja, nem a még nem létező új revíziót:
    assert sensor.native_value == "pending"
    assert sensor.extra_state_attributes["published_revision_id"] == 0

    # --- SUBTEST B: Sikeres commit és pillanatkép előrelépés ---
    db.commit_failure = False
    # Következő órai vagy kézi publikálás lefutása
    pub_res = await receiver.publish()

    assert receiver.ledger.data["pending"] is False
    assert receiver.ledger.data["published_revision_id"] == receiver.ledger.data["revision_id"]
    assert receiver.ledger.data["published_revision_id"] >= 2
    assert "2026-09-01" in receiver.ledger.data["published_daily_coverage"]

    # Szenzor frissült az új pillanatképpel:
    assert sensor.native_value == "synchronized"
    assert sensor.extra_state_attributes["published_revision_id"] == receiver.ledger.data["published_revision_id"]

    # --- SUBTEST C: In-flight deszinkronizációs védelem kliensoldalon ---
    client_cached_revision = sensor.extra_state_attributes["published_revision_id"]

    # Új adat érkezik és beíródik a háttérben:
    readings2 = [
        {"id": make_id(203), "meter_id": "gas_main", "captured_at": "2026-09-01T16:00:00+00:00", "value": "1002.000", "revision": 1, "source": "manual_review", "metadata": {}},
    ]
    await receiver.import_readings(ServiceCall(DOMAIN, "import_readings", {"readings": readings2}))
    new_db_revision = receiver.ledger.data["published_revision_id"]
    assert new_db_revision > client_cached_revision

    # Kliens válasz-érvényesítési logika:
    def client_validate_ws_response(client_snapshot_rev, response_db_rev):
        if client_snapshot_rev != response_db_rev:
            return "DISCARD_AND_RETRY"
        return "ACCEPTED"

    decision = client_validate_ws_response(client_cached_revision, new_db_revision)
    assert decision == "DISCARD_AND_RETRY"


@pytest.mark.asyncio
async def test_it04_rebuild_statistics_and_startup_recovery(ha_env):
    """IT-04: Rebuild törlés hibakezelés és megszakítás utáni helyreállítás a valós Receiver és async_setup használatával."""
    hass, db, store, receiver = ha_env

    # 48 órás mérés feltöltése (2026-09-01 08:00 - 2026-09-03 08:00)
    readings = [
        {"id": make_id(301), "meter_id": "gas_main", "captured_at": "2026-09-01T08:00:00+00:00", "value": "1000.000", "revision": 1, "source": "manual_review", "metadata": {}},
        {"id": make_id(302), "meter_id": "gas_main", "captured_at": "2026-09-03T08:00:00+00:00", "value": "1004.800", "revision": 1, "source": "manual_review", "metadata": {}},
    ]
    await receiver.import_readings(ServiceCall(DOMAIN, "import_readings", {"readings": readings}))
    stat_id = "gas_photo:gas_estimated"
    assert db.count_records(stat_id) == 48

    # --- SUBTEST A: Reprodukció a felhasználó által jelzett 2. hibára ---
    # Ha a Recorder nem rendelkezik törlési metódussal vagy a törlés meghiúsul,
    # a rebuild_statistics-nek hibát kell dobnia, és MEG KELL ŐRIZNIE a rebuild_in_progress=True állapotot!
    db.has_clear_method = False  # Nincs clear metódus (ahogy a HA 2026.7.2 Core Recorderében sem volt közvetlenül)

    with pytest.raises(ServiceValidationError, match="clear_statistics failed or unsupported"):
        await receiver.rebuild_statistics(ServiceCall(DOMAIN, "rebuild_statistics", {}))

    # Ellenőrzés: a rebuild_in_progress NEM tűnt el, megmaradt True-nak a Store-ban!
    assert receiver.ledger.data["rebuild_in_progress"] is True
    assert store.data["rebuild_in_progress"] is True

    # --- SUBTEST B: Sikeres újraépítés működő törlési mechanizmussal ---
    db.has_clear_method = True
    db.clear_failure = False

    rebuild_res = await receiver.rebuild_statistics(ServiceCall(DOMAIN, "rebuild_statistics", {}))
    assert rebuild_res["status"] == "rebuilt"
    assert rebuild_res["hours_count"] == 48

    # Sikeres újraépítés után a jelző törlődik:
    assert receiver.ledger.data["rebuild_in_progress"] is False
    assert store.data["rebuild_in_progress"] is False
    assert db.count_records(stat_id) == 48

    # --- SUBTEST C: Megszakítás utáni automatikus helyreállítás induláskor (async_setup) ---
    # Szimuláljuk, hogy a HA újraindul egy félbemaradt rebuild állapotban:
    interrupted_store_data = receiver.ledger.dump()
    interrupted_store_data["rebuild_in_progress"] = True
    store.data = interrupted_store_data

    # Töröljük a memóriabeli Receivert, mintha a HA épp most indulna el:
    hass.data.clear()

    # Lefuttatjuk a tényleges async_setup komponenst!
    config = {DOMAIN: {"max_m3_per_hour": 6}}
    setup_ok = await async_setup(hass, config)
    assert setup_ok is True

    # Megvárjuk az async_setup által elindított háttérfeladatokat
    await hass.async_block_till_done()

    # Ellenőrizzük, hogy az async_setup észlelte a félbemaradt állapotot és sikeresen befejezte:
    recovered_receiver = hass.data[DOMAIN]
    assert recovered_receiver.ledger.data.get("rebuild_in_progress", False) is False
    assert store.data.get("rebuild_in_progress", False) is False
    assert db.count_records(stat_id) == 48


@pytest.mark.asyncio
async def test_it05_websocket_command_readings(ha_env):
    """IT-05: Valós WebSocket lekérdezési parancs (websocket_readings) tesztelése."""
    hass, db, store, receiver = ha_env

    # Tesztadatok betöltése
    readings = [
        {"id": make_id(401), "meter_id": "gas_main", "captured_at": "2026-09-01T08:00:00+00:00", "value": "1000.000", "revision": 1, "source": "manual_review", "metadata": {}},
        {"id": make_id(402), "meter_id": "gas_main", "captured_at": "2026-09-01T12:00:00+00:00", "value": "1001.000", "revision": 1, "source": "manual_review", "metadata": {}},
    ]
    await receiver.import_readings(ServiceCall(DOMAIN, "import_readings", {"readings": readings}))

    # Mock WebSocket kapcsolat
    class MockConnection:
        def __init__(self):
            self.results = []
            self.errors = []
        def send_result(self, msg_id, result):
            self.results.append((msg_id, result))
        def send_error(self, msg_id, code, message):
            self.errors.append((msg_id, code, message))

    conn = MockConnection()

    # 1. Érvényes lekérdezés
    msg = {"id": 42, "type": "gas_photo/get_readings", "limit": 10, "offset": 0}
    websocket_readings(hass, conn, msg)

    assert len(conn.results) == 1
    assert conn.results[0][0] == 42
    readings_res = conn.results[0][1]["readings"]
    assert len(readings_res) == 2
    assert readings_res[0]["value"] == "1000.000"
    assert readings_res[1]["value"] == "1001.000"

    # 2. Helytelen tartomány lekérdezése (start > end)
    conn_err = MockConnection()
    bad_msg = {"id": 43, "type": "gas_photo/get_readings", "start": "not-a-valid-timestamp"}
    websocket_readings(hass, conn_err, bad_msg)
    assert len(conn_err.errors) == 1
    assert conn_err.errors[0][0] == 43
    assert conn_err.errors[0][1] == "invalid_format"
