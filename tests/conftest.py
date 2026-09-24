"""Pytest configuration and Home Assistant test harness setup.

Provides mock homeassistant package namespace and test fixtures so that
the actual custom_components.gas_photo integration components (Receiver, async_setup,
GasPhotoPublicationStatusSensor) can be executed natively in isolated tests.
"""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import types
from unittest.mock import AsyncMock, MagicMock
import pytest

# Ensure repository root is on sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


def setup_mock_ha():
    """Sets up mock homeassistant namespace in sys.modules if not already present."""
    if "homeassistant" in sys.modules:
        return

    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")

    class SupportsResponse:
        OPTIONAL = "optional"
        ONLY = "only"
        NONE = "none"

    class Context:
        def __init__(self, user_id="admin_user"):
            self.user_id = user_id

    class ServiceCall:
        def __init__(self, domain, service, data, context=None):
            self.domain = domain
            self.service = service
            self.data = data
            self.context = context or Context()

    class HomeAssistant:
        def __init__(self):
            self.data = {}
            self.services = MockServices()
            self.auth = MockAuth()
            self.http = MockHttp()
            self._tasks = []

        @property
        def loop(self):
            try:
                return asyncio.get_running_loop()
            except RuntimeError:
                if not hasattr(self, "_fallback_loop"):
                    self._fallback_loop = asyncio.new_event_loop()
                return self._fallback_loop

        def async_create_task(self, coro):
            t = asyncio.create_task(coro)
            self._tasks.append(t)
            return t

        async def async_add_executor_job(self, func, *args):
            return await self.loop.run_in_executor(None, func, *args)

        async def async_block_till_done(self):
            if self._tasks:
                await asyncio.gather(*[t for t in self._tasks if not t.done()], return_exceptions=True)

    def callback(f):
        return f

    core.HomeAssistant = HomeAssistant
    core.ServiceCall = ServiceCall
    core.SupportsResponse = SupportsResponse
    core.callback = callback
    ha.core = core
    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = core

    comps = types.ModuleType("homeassistant.components")
    ws = types.ModuleType("homeassistant.components.websocket_api")
    ws.websocket_command = lambda schema: lambda f: f
    ws.async_response = lambda f: f
    ws_commands = {}

    def async_register_command(hass, handler):
        cmd_type = getattr(handler, "_ws_command_type", None)
        ws_commands[cmd_type] = handler

    ws.async_register_command = async_register_command
    ws._commands = ws_commands
    comps.websocket_api = ws
    sys.modules["homeassistant.components"] = comps
    sys.modules["homeassistant.components.websocket_api"] = ws

    rec_models = types.ModuleType("homeassistant.components.recorder.models")
    class StatisticMeanType:
        NONE = 0
        ARITHMETIC = 1
    rec_models.StatisticMeanType = StatisticMeanType
    sys.modules["homeassistant.components.recorder.models"] = rec_models

    rec = types.ModuleType("homeassistant.components.recorder")
    rec.get_instance = lambda hass: getattr(hass, "recorder", None)
    comps.recorder = rec
    sys.modules["homeassistant.components.recorder"] = rec

    rec_stats = types.ModuleType("homeassistant.components.recorder.statistics")
    def async_add_external_statistics(hass, metadata, statistics):
        recorder = getattr(hass, "recorder", None)
        if recorder:
            recorder.enqueue_statistics(metadata["statistic_id"], statistics)

    def statistics_during_period(hass, start, end, statistic_ids, period="hour", units=None, types=None):
        recorder = getattr(hass, "recorder", None)
        if recorder:
            return recorder.statistics_during_period(list(statistic_ids)[0], start, end)
        return {}

    rec_stats.async_add_external_statistics = async_add_external_statistics
    rec_stats.statistics_during_period = statistics_during_period
    sys.modules["homeassistant.components.recorder.statistics"] = rec_stats

    sensor_mod = types.ModuleType("homeassistant.components.sensor")
    class SensorEntity:
        hass = None
        entity_id = None
        _attr_name = None
        _attr_unique_id = None
        def async_write_ha_state(self):
            pass

    class SensorDeviceClass:
        GAS = "gas"

    sensor_mod.SensorEntity = SensorEntity
    sensor_mod.SensorDeviceClass = SensorDeviceClass
    comps.sensor = sensor_mod
    sys.modules["homeassistant.components.sensor"] = sensor_mod

    http = types.ModuleType("homeassistant.components.http")
    class StaticPathConfig:
        def __init__(self, url_path, path, cache_headers=False):
            self.url_path, self.path, self.cache_headers = url_path, path, cache_headers
    http.StaticPathConfig = StaticPathConfig
    comps.http = http
    sys.modules["homeassistant.components.http"] = http

    exc = types.ModuleType("homeassistant.exceptions")
    class ServiceValidationError(Exception): pass
    class Unauthorized(Exception): pass
    exc.ServiceValidationError = ServiceValidationError
    exc.Unauthorized = Unauthorized
    sys.modules["homeassistant.exceptions"] = exc

    helpers = types.ModuleType("homeassistant.helpers")
    cv = types.ModuleType("homeassistant.helpers.config_validation")
    cv.positive_int = lambda v: int(v) if int(v) >= 0 else (_ for _ in ()).throw(ValueError("positive_int"))
    cv.string = lambda v: str(v)
    helpers.config_validation = cv
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.config_validation"] = cv

    disc = types.ModuleType("homeassistant.helpers.discovery")
    disc.async_load_platform = lambda *a, **k: asyncio.sleep(0)
    helpers.discovery = disc
    sys.modules["homeassistant.helpers.discovery"] = disc

    disp = types.ModuleType("homeassistant.helpers.dispatcher")
    _signals = {}
    def async_dispatcher_send(hass, signal, *args):
        for cb in _signals.get(signal, []):
            cb(*args)
    def async_dispatcher_connect(hass, signal, target):
        _signals.setdefault(signal, []).append(target)
        return lambda: _signals[signal].remove(target)
    disp.async_dispatcher_send = async_dispatcher_send
    disp.async_dispatcher_connect = async_dispatcher_connect
    helpers.dispatcher = disp
    sys.modules["homeassistant.helpers.dispatcher"] = disp

    event = types.ModuleType("homeassistant.helpers.event")
    event.async_track_time_change = lambda *a, **k: None
    helpers.event = event
    sys.modules["homeassistant.helpers.event"] = event

    storage = types.ModuleType("homeassistant.helpers.storage")
    class MockStore:
        def __init__(self, hass, version, key):
            self.hass = hass
            self.version = version
            self.key = key

        async def async_load(self):
            store_dict = getattr(self.hass, "_mock_storage", {})
            val = store_dict.get(self.key)
            return json.loads(json.dumps(val, default=str)) if val is not None else None

        async def async_save(self, data):
            if not hasattr(self.hass, "_mock_storage"):
                self.hass._mock_storage = {}
            self.hass._mock_storage[self.key] = json.loads(json.dumps(data, default=str))

        @property
        def data(self):
            return getattr(self.hass, "_mock_storage", {}).get(self.key)

        @data.setter
        def data(self, val):
            if not hasattr(self.hass, "_mock_storage"):
                self.hass._mock_storage = {}
            self.hass._mock_storage[self.key] = val

    storage.Store = MockStore
    helpers.storage = storage
    sys.modules["homeassistant.helpers.storage"] = storage


class MockServices:
    def __init__(self):
        self.services = {}

    def async_register(self, domain, service, handler, schema=None, supports_response=None):
        self.services.setdefault(domain, {})[service] = {
            "handler": handler,
            "schema": schema,
            "supports_response": supports_response,
        }

    async def async_call(self, domain, service, data=None, context=None):
        entry = self.services.get(domain, {}).get(service)
        if not entry:
            raise KeyError(f"Service {domain}.{service} not registered")
        from homeassistant.core import ServiceCall
        call = ServiceCall(domain, service, data or {}, context)
        handler = entry["handler"]
        if asyncio.iscoroutinefunction(handler):
            return await handler(call)
        return handler(call)


class MockUser:
    def __init__(self, is_admin=True):
        self.is_admin = is_admin


class MockAuth:
    async def async_get_user(self, user_id):
        if user_id == "non_admin":
            return MockUser(is_admin=False)
        return MockUser(is_admin=True)


class MockHttp:
    async def async_register_static_paths(self, paths):
        pass


class IsolatedRecorderDB:
    """Real SQLite database implementing Home Assistant recorder schema."""

    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

        # Queue for async_add_external_statistics
        self.queue = []
        self.commit_failure = False
        self.commit_count = 0
        self.clear_failure = False
        self.has_clear_method = True

    def _init_schema(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS statistics_meta (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    statistic_id VARCHAR(255) UNIQUE NOT NULL,
                    source VARCHAR(32) NOT NULL,
                    unit_of_measurement VARCHAR(32),
                    has_mean BOOLEAN NOT NULL DEFAULT 0,
                    has_sum BOOLEAN NOT NULL DEFAULT 1,
                    name VARCHAR(255)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_ts FLOAT NOT NULL,
                    metadata_id INTEGER NOT NULL REFERENCES statistics_meta(id),
                    start_ts FLOAT NOT NULL,
                    mean FLOAT,
                    min FLOAT,
                    max FLOAT,
                    last_reset_ts FLOAT,
                    state FLOAT,
                    sum FLOAT,
                    CONSTRAINT unique_meta_start UNIQUE (metadata_id, start_ts)
                )
            """)

    def get_or_create_metadata(self, statistic_id: str, source: str = "gas_photo") -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM statistics_meta WHERE statistic_id = ?", (statistic_id,))
        row = cur.fetchone()
        if row:
            return row["id"]
        with self.conn:
            cur.execute("""
                INSERT INTO statistics_meta (statistic_id, source, unit_of_measurement, has_mean, has_sum, name)
                VALUES (?, ?, 'm³', 0, 1, ?)
            """, (statistic_id, source, statistic_id))
            return cur.lastrowid

    def enqueue_statistics(self, statistic_id: str, statistics: list) -> None:
        """Called by async_add_external_statistics - adds items to recorder queue."""
        self.queue.append((statistic_id, statistics))

    async def async_block_till_done(self) -> None:
        """Simulates recorder queue processing and committing to SQLite database."""
        if self.commit_failure:
            raise RuntimeError("Database connection lost during recorder commit")

        now_ts = datetime.now(timezone.utc).timestamp()
        with self.conn:
            for stat_id, stats in self.queue:
                meta_id = self.get_or_create_metadata(stat_id)
                for s in stats:
                    dt = s["start"] if isinstance(s["start"], datetime) else datetime.fromisoformat(str(s["start"]))
                    start_ts = dt.astimezone(timezone.utc).timestamp()
                    state = float(s["state"]) if s.get("state") is not None else None
                    sum_val = float(s["sum"]) if s.get("sum") is not None else None
                    self.conn.execute("""
                        INSERT INTO statistics (created_ts, metadata_id, start_ts, state, sum)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(metadata_id, start_ts) DO UPDATE SET
                            state = excluded.state,
                            sum = excluded.sum
                    """, (now_ts, meta_id, start_ts, state, sum_val))
        self.queue.clear()
        self.commit_count += 1

    def async_clear_statistics(self, statistic_ids: list[str], *, on_done=None) -> None:
        """Simulates HA Core PR #127120 async_clear_statistics with callback."""
        if not self.has_clear_method:
            raise AttributeError("Recorder has no attribute async_clear_statistics")
        if self.clear_failure:
            # Simulate failure (do not call on_done or raise)
            raise RuntimeError("Clear statistics failed in recorder thread")

        with self.conn:
            for sid in statistic_ids:
                self.conn.execute("""
                    DELETE FROM statistics
                    WHERE metadata_id IN (SELECT id FROM statistics_meta WHERE statistic_id = ?)
                """, (sid,))
        if on_done:
            on_done()

    def statistics_during_period(self, statistic_id: str, start: datetime, end: datetime) -> dict:
        start_ts = start.astimezone(timezone.utc).timestamp()
        end_ts = end.astimezone(timezone.utc).timestamp()
        cur = self.conn.cursor()
        cur.execute("""
            SELECT s.start_ts, s.state, s.sum
            FROM statistics s
            JOIN statistics_meta sm ON s.metadata_id = sm.id
            WHERE sm.statistic_id = ?
              AND s.start_ts >= ?
              AND s.start_ts < ?
            ORDER BY s.start_ts ASC
        """, (statistic_id, start_ts, end_ts))
        rows = cur.fetchall()
        result = []
        for i, row in enumerate(rows):
            dt = datetime.fromtimestamp(row["start_ts"], tz=timezone.utc)
            prev_sum = rows[i-1]["sum"] if i > 0 else None
            change = (row["sum"] - prev_sum) if prev_sum is not None else None
            result.append({
                "start": dt.isoformat(),
                "start_ts": row["start_ts"],
                "state": row["state"],
                "sum": row["sum"],
                "change": change,
            })
        return {statistic_id: result}

    def count_records(self, statistic_id: str) -> int:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT COUNT(*) as cnt
            FROM statistics s
            JOIN statistics_meta sm ON s.metadata_id = sm.id
            WHERE sm.statistic_id = ?
        """, (statistic_id,))
        return cur.fetchone()["cnt"]

    def close(self):
        self.conn.close()


setup_mock_ha()
