# Changelog

A projekt változásai a [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) irányelvei szerint dokumentálva.

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
