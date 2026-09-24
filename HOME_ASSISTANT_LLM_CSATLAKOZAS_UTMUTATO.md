# Útmutató: Hogyan kapcsolódhat egy LLM a Home Assistanthoz a megállapítások és kódok validálására? (v5.0 - Pontosított mérnöki kiadás)

## 1. Bevezetés: A telemetria szerepe és a tévedhetetlenségi mítosz eloszlatása

Amikor egy nagy nyelvi modell (LLM / kódoló ágens) egy Home Assistant rendszeren dolgozik, **alapvető különbség van az elméleti válaszadás és az igazolt rendszermérnöki munka között**:
* **Elméleti működés (Vak tanácsadás):** Az LLM a képzési adataiból generál YAML konfigurációkat. Nem látja az élő adatbázis sémáját, a futó Core verziót (pl. 2026.7.2), sem az egyedi integrációk belső viselkedését, így könnyen javasolhat olyan logikát, amely deadlockot, adatvesztést vagy duplikált számlázást okoz.
* **Telemetriával támogatott működés (Agentic Pair-Programming):** Az LLM API-kon és eszközökön keresztül képes **beolvasni a valós konfigurációt**, **lekérdezni az aktuális állapotokat**, és **ellenőrzéseket futtatni**.

> [!IMPORTANT] Mérnöki valóság: Nincs „garantált tévedhetetlenség”
> A közvetlen API- vagy SSH-kapcsolat **önmagában nem teszi tévedhetetlenné az LLM-et**. Ahogy a korábbi tervezési körök is bizonyították: az LLM matematikai hibát véthet (pl. kerekítési hibát generálhat), és félreértelmezheti az adatfolyamokat. A megbízhatóság záloga nem a modell „intelligenciája”, hanem a **szigorú, többszintű tesztelési fegyelem (Unit tesztek, integrációs visszaolvasás, számszerű dashboard-audit)**.

---

## 2. A Helyi MCP Környezet Valós Képességei (`ha_mcp_server.py` forráskód-audit)

A nálad futó, FastMCP alapú [`ha_mcp_server.py`](file:///Users/kasnyiklaszlo/_DEV/_HOME_ASSISTANT_AI/ha_mcp_server.py) szerver az alábbi konkrét eszközöket biztosítja a fejlesztő ágens számára:

### 2.1. `get_states` (REST API wrapper)
* **Valós megvalósítás:** A Home Assistant `GET /api/states` végpontját hívja le HTTPX klienssel.
* **Visszatérési érték:** Nem fogad `entity_id` szűrőt; a teljes állapothalmazból egy **JSON-szövegként formázott listát** állít elő, amely elemenként 3 mezőt tartalmaz:
  ```json
  [
    {
      "entity_id": "sensor.gas_photo_latest_reading",
      "state": "1901.341",
      "friendly_name": "Gas photo latest reading"
    },
    ...
  ]
  ```
* **Korlát:** Nem adja vissza a teljes attribútum-struktúrát és az időbélyegeket (`last_updated`, `last_changed`). Ha az LLM-nek részletes attribútumokra van szüksége, közvetlen REST lekérdezést kell tennie.

### 2.2. `call_service` (REST API wrapper)
* **Valós megvalósítás:** A `POST /api/services/{domain}/{service}` végpontot hívja meg.
* **Paraméterezés:** `domain: str`, `service: str`, opcionális `entity_id: str`, és `service_data: str` (JSON-formátumú szövegként átadva).
* **Korlát:** A wrapper jelenleg csak sikeres HTTP státuszkódot igazol vissza, a HTTP válasz törzsét eldobja, és nem adja át a `?return_response=true` paramétert. Ezért a Home Assistant válaszadó szolgáltatásai (Response Data, pl. `recorder.get_statistics` vagy `gas_photo.get_readings`) közvetlen kiolvasására nem alkalmas ezen a wrapperen keresztül.

### 2.3. `read_config` (SSH wrapper)
* **Valós megvalósítás:** SSH kapcsolaton keresztül futtatja a `cat {HA_CONFIG_PATH}/{filename}` parancsot.
* **Megfontolás:** A fájlnevet közvetlenül shell-parancsba fűzi; a script `StrictHostKeyChecking=no` beállítást használ, amely helyi tesztkörnyezetben gyors, de termelésben ismert host-key hitelesítést igényel.

### 2.4. `write_config` (SCP wrapper)
* **Valós megvalósítás:** Nem shell `cat << EOF`-ot használ, hanem a gépen létrehoz egy ideiglenes fájlt (`tempfile.NamedTemporaryFile`), kiírja a tartalmat, majd **biztonságos fájlmásolással (SCP)** juttatja el a célgépre:
  `scp -o StrictHostKeyChecking=no -P {PORT} {temp_path} {USER}@{HOST}:{filepath}`
  A sikeres átvitel után az ideiglenes helyi fájlt azonnal törli (`os.remove(temp_path)`).
* **Megjegyzés:** Az SCP parancssorban is expliciten ki van kapcsolva a host-key ellenőrzés (`StrictHostKeyChecking=no`).

---

## 3. A 4 Fő Csatlakozási Mód a Gyakorlatban

```mermaid
flowchart LR
    subgraph Kliens [LLM / AI Fejlesztő Kliens]
        Agent[Kódoló Ágens]
    end

    subgraph MCP_Eszkozok [MCP Wrapper Réteg]
        MCP_State[get_states]
        MCP_Service[call_service]
        MCP_Read[read_config: SSH cat]
        MCP_Write[write_config: SCP tempfile]
    end

    subgraph Native_APIs [Közvetlen HA Interfészek]
        REST[REST API<br/>Bearer Token]
        WS[WebSocket API<br/>Statistics & Events]
        SSH[SSH CLI<br/>ha core check / logs]
    end

    subgraph HA_Core [Home Assistant Core]
        Engine[State Engine]
        Recorder[(Recorder LTS DB)]
        FS[/config Könyvtár]
    end

    Agent --> MCP_State & MCP_Service & MCP_Read & MCP_Write
    MCP_State & MCP_Service --> REST
    MCP_Read --> SSH
    MCP_Write --> SSH

    Agent -.->|Recorder visszaolvasás| WS
    Agent -.->|Service response / check| REST
    Agent -.->|Rendszergazdai CLI| SSH

    REST --> Engine
    WS --> Recorder
    SSH --> FS
```

### 1. REST API — Állapotok és Konfiguráció-ellenőrzés
* **Konfiguráció-ellenőrzés hatóköre:** `POST /api/config/core/check_config` — a Core komponens sémáját és a YAML szintaxist ellenőrzi (`components/config/core.py`).
  > [!WARNING] Nem teljes entitás-ellenőrzés
  > A `check_config` **NEM ellenőrzi a futásidejű entitásfüggőségeket** (pl. ha egy automatizációban egy nem létező entitás ID-ra hivatkozol, a `check_config` továbbra is `valid` választ ad). A működőképességet élő teszttel kell igazolni.
* **Szolgáltatás-alapú statisztika visszaolvasás:** A Recorder adatai REST-en is lekérdezhetők a `recorder.get_statistics` szolgáltatással:
  ```http
  POST /api/services/recorder/get_statistics?return_response=true
  Authorization: Bearer <TOKEN>
  Content-Type: application/json

  {
    "statistic_ids": ["gas_photo:gas_estimated"],
    "period": "day",
    "types": ["change", "sum"],
    "start_time": "2026-09-24T00:00:00Z"
  }
  ```
  *Fontos válaszútvonal:* A Home Assistant Core `recorder.get_statistics` szolgáltatása az eredményt a `statistics` szótárba ágyazza, amit a REST kezelő a `service_response` wrapper alá tesz. A pontos elérés a kliensben:
  `response["service_response"]["statistics"]["gas_photo:gas_estimated"]`

### 2. WebSocket API — Statisztikák és Élő Események
* A Recorder hosszú távú statisztikáinak lekérdezése:
  ```json
  {
    "id": 1,
    "type": "recorder/statistics_during_period",
    "start_time": "2026-09-24T00:00:00Z",
    "end_time": "2026-09-24T23:59:59Z",
    "statistic_ids": ["gas_photo:gas_estimated"],
    "period": "hour",
    "types": ["change", "sum"]
  }
  ```

### 3. SSH és Home Assistant CLI
* `ha core check`: Rendszerkonfiguráció ellenőrzése parancssorból.
* `ha core restart`: Rendszer újraindítása.
* `ha core logs`: Hibakeresés a komponensek indulásakor.

---

## 4. A Háromszintű Validációs Módszertan

1. **1. Szint: Elszigetelt Unit Tesztek (Kiszámítási logika):**
   - Tiszta Python tesztkörnyezetben fut, nulla HA függőséggel.
   - Matematikai invariánsok ellenőrzése (Teleszkópos Kumulatív Integrálás: $\sum \text{bucket\_delta} \equiv \Delta V$).
   - Időzónás határok (`Europe/Budapest`, 23 és 25 órás DST napok).
   - Tört órás átfedések, kétoldali zárt órák szűrése, rekordkorrekció és explicit mérőcsere.
2. **2. Szint: Elkülönített HA Integrációs Tesztek (Izolált Tesztpéldány):**
   - **Kizárólag külön teszt-adatbázisban vagy teszt-konténerben végezhető**, megvédve az éles adatbázist a visszavonhatatlan sortörlések hiányától.
   - Valódi `async_add_external_statistics` hívás a `gas_photo:gas_estimated` sorozatba.
   - Adatbázis visszaolvasás (REST `service_response` vagy WebSocket): az órás határok és kumulált értékek ellenőrzése.
   - Idempotencia teszt: az ismételt publikálás nem duplikálhat sorokat.
3. **3. Szint: Megjelenítési és Dashboard Audit (UI konzisztencia):**
   - Napi oszlopdiagram, havi átlag és keretkártyák számszerű egyezése azonos adatlezárási időpontig.
   - Utólagos korrekció tesztje a revíziókövető trigger entitás segítségével.

---

## 5. Összegzés

Az LLM nem csodafegyver, hanem egy nagy sebességű fejlesztő partner. A Home Assistant integrációja (MCP, REST, SSH) akkor nyújt valódi értéket, ha a modell állításait **nem fogadjuk el bemondásra**, hanem a rendszeren közvetlenül futtatott **többszintű tesztekkel és számszerű bizonyítékokkal igazoltatjuk**.
