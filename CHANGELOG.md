# Changelog

A projekt változásai a [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) irányelvei szerint dokumentálva.

---

## [1.1.0] - 2026-09-24

### ✨ Hozzáadva (Added)

- **Determinisztikus teleszkópos gázinterpoláció (`interpolation.py`)**:
  - Tiszta Python implementáció külső HA függőség nélkül (`Decimal`, `datetime`, `zoneinfo`).
  - Szigorú `Decimal('0.001')` felbontás és összegmegőrzés (`ROUND_HALF_UP`), zéró lebegőpontos maradékhiba.
  - Globális intervallum-unió lefedettség (`CLOSED`, `PARTIAL`, `OPEN`) `Europe/Budapest` naptári napok szerint.
  - Kétoldali egész óra szűrés $[h, h+3600\text{s}) \subseteq \mathcal{U}$.
- **Új LTS statisztikai sorozat (`gas_photo:gas_estimated`)**:
  - Hosszú távú órás becsült gázfogyasztási profil közzététele a Home Assistant Recorder adatbázisába.
- **Többkorszakos mérőcsere támogatás (`meter_replacement`)**:
  - Új szolgáltatás: `gas_photo.meter_replacement` (`old_final_reading`, `new_initial_reading`, `replacement_time`).
  - Cserén átívelő órán belüli összeadás (intra-hour slice aggregation), negatív fogyasztás fizikai kizárása.
  - Régi `hourly()` és új `estimated_hourly()` összegmegőrzés kumulált mérő-eltolással.
- **Publikációs tranzakciós szinkronizáció és új állapotfigyelő szenzor**:
  - `sensor.gas_photo_publication_status` entitás a közzétételi állapot és pillanatkép követésére (`published_revision_id`, `published_daily_coverage`).
  - Recorder commit bevárása (`await rec.async_block_till_done()`) a pillanatkép előreléptetése előtt.
  - In-flight deszinkronizációs védelem aszinkron kliensek számára.
- **Megbízható statisztika-újraépítés (`rebuild_statistics`)**:
  - Core PR #127120 aszinkron WebSocket törléskezelés és hibamegőrzés.
  - Megszakadt folyamat automatikus észlelése és folytatása induláskor (`async_setup`).
- **Háromszintű automatizált tesztcsomag (`tests/`)**:
  - 16 tiszta Python / Decimal unit és regressziós teszt (UT-01..UT-10).
  - 5 izolált integrációs teszt valós komponensekkel (`Receiver`, `Store`, `Sensor`, `WebSocket`, `async_setup`).
  - 4 dashboard audit teszt, natív Node.js V8 futtatás és Puppeteer böngészős vizuális audit.

---

## [1.0.0] - 2026-09-12

### ✨ Hozzáadva (Added)

- **Dedikált Home Assistant egyedi integráció (`gas_photo`)**:
  - Önálló HACS-kompatibilis és manuálisan telepíthető struktúra.
- **REST & WebSocket API**:
  - `POST /api/services/gas_photo/import_readings`: Leolvasási rekordok biztonságos importálása egyedi azonosítóval, időbélyeggel és revíziókövetéssel.
  - `POST /api/services/gas_photo/get_readings` és websocket parancs a leolvasások lapozható visszaolvasásához.
- **Megbízható leolvasási napló (`ledger.py`)**:
  - Tranzakciós perzisztencia a `.storage/gas_photo.ledger` fájlban.
  - Idempotens feldolgozás és csökkenő fogyasztás elleni fizikai validáció.
  - Óránkénti fogyasztási küszöb (`max_m3_per_hour`) védelem.
- **Home Assistant Energy Dashboard kompatibilitás**:
  - Külső statisztika entitás generálása: `gas_photo:gas_main` (`m³`, `has_sum: true`).
  - Hivatalos Home Assistant energiastatisztikák órás göngyölítéssel.
- **Beépített Lovelace kártya (`static/gas-photo-card.js`)**:
  - Külső CDN nélküli, helyben kiszolgált egyedi irányítópult kártya a pontos leolvasások megjelenítésére és lapozására.
- **Kereszthivatkozás és integráció a GasPhotoIOS klienssel**:
  - Teljes kompatibilitás a [GAS_METER_READER_IOS](https://github.com/M3NT1/GAS_METER_READER_IOS) iPhone alkalmazással.
