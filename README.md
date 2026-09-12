# Home Assistant Gas Photo Receiver (`gas_photo`)

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Custom%20Integration-41BDF5.svg?style=flat-square&logo=home-assistant)](https://www.home-assistant.io/)
[![HACS](https://img.shields.io/badge/HACS-Custom%20Repository-orange.svg?style=flat-square)](https://hacs.xyz/)
[![Companion App](https://img.shields.io/badge/iOS%20Client-GAS__METER__READER__IOS-blue.svg?style=flat-square&logo=apple)](https://github.com/M3NT1/GAS_METER_READER_IOS)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)

A **`gas_photo`** egy megbízható, dedikált egyedi integráció (Custom Integration) **Home Assistant** alá, amely fogadja, hitelesíti és változtathatatlan naplóban (`ledger`) tárolja a digitális gázóra-leolvasásokat, valamint óránkénti fogyasztási statisztikákat generál a Home Assistant **Energy Dashboard** (Energia irányítópult) számára.

---

## 🔗 Kapcsolódó Kliensalkalmazás (Kötelező a teljes működéshez!)

> [!IMPORTANT]
> Ez az integráció a szerveroldali fogadóállomás. A gázóra helyszíni lefotózásához, az automatikus gépi látásos keret- és számjegy-felismeréshez, valamint az egyérintéses REST API beküldéshez használd a natív iOS társalkalmazást:
>
> 📱 **[GasPhotoIOS — GAS_METER_READER_IOS](https://github.com/M3NT1/GAS_METER_READER_IOS)**
>
> A két tároló együtt alkotja a teljeskörű, 100%-ban helyi adatvédelemmel működő gázóra-digitalizáló rendszert.

---

## 🌟 Fő Jellemzők

1. **Biztonságos REST és WebSocket API**:
   - `POST /api/services/gas_photo/import_readings`: Leolvasások biztonságos fogadása Long-Lived Access Token hitelesítéssel.
   - Idempotens feldolgozás egyedi SHA-256 hash és időbélyeg alapján (nincs duplikáció).
   - `gas_photo/get_readings`: Visszaolvasási és audit API.

2. **Változtathatatlan Főkönyv (`.storage/gas_photo.ledger`)**:
   - Tranzakcionális és perzisztens tárolás.
   - Fizikai érvényesség-ellenőrzés: megelőzi a negatív fogyasztást (csökkenő óraállást) és a konfigurálható maximális óránkénti fogyasztást (`max_m3_per_hour`).

3. **Közvetlen Home Assistant Energy Dashboard Integráció**:
   - Külső statisztika entitás: **`gas_photo:gas_main`** (`m³`, `has_sum: true`).
   - Órás göngyölített fogyasztási adatok előállítása a hivatalos gázfogyasztási panelekhez.

4. **Beépített Lovelace Kártya (`custom:gas-photo-card`)**:
   - Pontos előzménynapló kártya lapozással, időbélyegekkel, leolvasási értékekkel és egymás közötti különbségekkel.

---

## 📥 Telepítés

### 1. Opció: Telepítés HACS segítségével (Ajánlott)
1. Nyisd meg a **HACS**-ot a Home Assistantban.
2. Kattints a jobb felső sarokban a **három pontra** ➔ **Custom repositories** (Egyedi tárolók).
3. Másold be az alábbi URL-t:
   ```
   https://github.com/M3NT1/home-assistant-gas-photo
   ```
4. Kategóriaként válaszd: **Integration**.
5. Kattints az **Add** gombra, majd keresd meg és telepítsd a **Gas Photo** integrációt.
6. Indítsd újra a Home Assistantot.

### 2. Opció: Manuális telepítés
1. Töltsd le a legfrissebb kiadást a [Releases](https://github.com/M3NT1/home-assistant-gas-photo/releases) oldalról.
2. Másold a `custom_components/gas_photo` könyvtárat a Home Assistant telepítésed `/config/custom_components/` mappájába:
   ```
   /config/custom_components/gas_photo/
   ├── __init__.py
   ├── manifest.json
   ├── sensor.py
   ├── services.yaml
   ├── ledger.py
   └── static/
       └── gas-photo-card.js
   ```
3. Indítsd újra a Home Assistantot.

---

## ⚙️ Konfiguráció

Add hozzá a következő sort a `configuration.yaml` fájlodhoz:

```yaml
gas_photo:
  max_m3_per_hour: 6   # A gázórád fizikai maximális átfolyása (m³/óra)
```

Ezután menj a **Fejlesztői eszközök** ➔ **YAML** menübe és kattints a **Minden YAML-konfiguráció újratöltése** gombra (vagy indítsd újra a Home Assistantot).

---

## 📲 Használat a GasPhotoIOS iPhone alkalmazással

1. A Home Assistantban hozz létre egy **Hosszú élettartamú hozzáférési tokent** (Profil ➔ Biztonság ➔ Hosszú élettartamú hozzáférési tokenek).
2. Nyisd meg az iPhone-odon a **[GasPhotoIOS](https://github.com/M3NT1/GAS_METER_READER_IOS)** appot.
3. A **Beállítások** fülön add meg a Home Assistant címedet (pl. `http://homeassistant.local:8123` vagy Tailscale / Nabu Casa URL) és illeszd be a tokent.
4. Készíts egy fotót az óráról, ellenőrizd az AI által felismert 8 számjegyet, majd érintsd meg a **„Mentés és Szinkronizálás”** gombot.
5. Az adat azonnal beérkezik a Home Assistantba, és megjelenik a statisztikákban!

---

## 📊 Energy Dashboard Beállítása

1. Lépj be a Home Assistant **Áttekintés** ➔ **Energia** menüjébe.
2. Kattints a jobb felső három pontra ➔ **Energia irányítópult beállítása**.
3. A **Gázfogyasztás** szekcióban kattints a **Gázforrás hozzáadása** lehetőségre.
4. Válaszd ki a **`gas_photo:gas_main`** entitást.
5. Mentsd el. Az Energia panel óránkénti felbontásban fogja mutatni a gázfogyasztásodat.

---

## 📋 Lovelace Kártya Hozzáadása

A beépített naplókártya megjelenítéséhez:
1. **Beállítások** ➔ **Irányítópultok** ➔ **Erőforrások** alatt add hozzá:
   - URL: `/gas_photo/gas-photo-card.js`
   - Típus: `JavaScript-modul`
2. Bármelyik irányítópulton adj hozzá egy manuális kártyát:
   ```yaml
   type: custom:gas-photo-card
   title: Pontos gázóra-leolvasások
   ```

---

## 📄 Licenc

Ez a projekt nyílt forráskódú, az [MIT Licenc](LICENSE) feltételei szerint használható.
