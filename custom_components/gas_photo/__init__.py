"""YAML gas photo receiver; exact Store ledger is authoritative."""
import asyncio
import logging
from pathlib import Path
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.components import websocket_api
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import async_add_external_statistics, statistics_during_period
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from .ledger import Ledger, timestamp

DOMAIN = 'gas_photo'
_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({vol.Optional('max_m3_per_hour', default=6): vol.All(vol.Coerce(float), vol.Range(min=0.001,max=1000))})}, extra=vol.ALLOW_EXTRA)
QUERY_FIELDS = {vol.Optional('offset',default=0): vol.All(cv.positive_int,vol.Range(max=1000000)),vol.Optional('limit',default=500):vol.All(cv.positive_int,vol.Range(min=1,max=500)),vol.Optional('start'):cv.string,vol.Optional('end'):cv.string,vol.Optional('ids'):vol.All([cv.string],vol.Length(max=100))}
METADATA = {'statistic_id':'gas_photo:gas_main','source':DOMAIN,'name':'Gas photo · observation-hour consumption','unit_of_measurement':'m³','unit_class':'volume','mean_type':StatisticMeanType.NONE,'has_sum':True}

class Receiver:
    def __init__(self,hass,store,ledger):
        self.hass,self.store,self.ledger=hass,store,ledger
        self.lock=asyncio.Lock()

    def query(self,data):
        if 'ids' in data:
            ids=set(data['ids'])
            rows=[r for r in self.ledger._all() if r['id'] in ids]
            return {'readings':rows}
        try:
            rows=self.ledger.readings(**{k:v for k,v in data.items() if k in {'offset','limit','start','end'}})
        except ValueError as exc: raise ServiceValidationError(str(exc)) from exc
        return {'readings':rows}

    async def statistics(self,data):
        try:
            start,end=timestamp(data['start']),timestamp(data['end'])
            if not 0 < (end-start).total_seconds() <= 31*86400:
                raise ValueError('Statistics readback requires a range of at most 31 days')
        except ValueError as exc: raise ServiceValidationError(str(exc)) from exc
        result=await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,self.hass,start,end,{'gas_photo:gas_main'},'hour',None,{'state','sum'})
        expected=[r for r in self.ledger.hourly() if start <= timestamp(r['start']) < end]
        return {'statistic_id':'gas_photo:gas_main','statistics':result.get('gas_photo:gas_main',[]),'expected':expected}

    async def publish(self,*_):
        async with self.lock:
            await self._publish_locked()

    async def _publish_locked(self):
        # Lock baseline/timestamps and persist pending BEFORE any recorder queue call.
        candidate=Ledger(self.ledger.dump(),self.ledger.max_rate)
        rows=candidate.prepare_publication()
        await self.store.async_save(candidate.dump())
        self.ledger=candidate
        try:
            for offset in range(0,len(rows),500):
                batch=[{**r,'start':timestamp(r['start'])} for r in rows[offset:offset+500]]
                async_add_external_statistics(self.hass,METADATA,batch)
        except Exception:
            _LOGGER.exception('Statistics queue failed; exact ledger saved, retry at next hour/startup')
            return 'pending'
        # pending deliberately stays true: queueing is not a commit acknowledgement.
        return 'queued' if rows else 'pending'

    async def import_readings(self,call):
        user=await self.hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        if user is None or not user.is_admin: raise Unauthorized()
        async with self.lock:
            candidate=Ledger(self.ledger.dump(),self.ledger.max_rate)
            try: accepted=candidate.apply(call.data['readings'])
            except ValueError as exc: raise ServiceValidationError(str(exc)) from exc
            await self.store.async_save(candidate.dump())
            self.ledger=candidate
            async_dispatcher_send(self.hass,DOMAIN+'_updated')
            status=await self._publish_locked()
        return {'accepted':accepted,'statistics_status':status}

@websocket_api.websocket_command({vol.Required('type'):'gas_photo/get_readings',**QUERY_FIELDS})
@callback
def websocket_readings(hass,connection,msg):
    try: result=hass.data[DOMAIN].query(msg)
    except ServiceValidationError as exc:
        connection.send_error(msg['id'],'invalid_format',str(exc)); return
    connection.send_result(msg['id'],result)

async def async_setup(hass: HomeAssistant,config):
    store=Store(hass,1,DOMAIN+'.ledger')
    ledger=Ledger(await store.async_load(),config[DOMAIN]['max_m3_per_hour'])
    receiver=Receiver(hass,store,ledger)
    hass.data[DOMAIN]=receiver
    hass.services.async_register(DOMAIN,'import_readings',receiver.import_readings,schema=vol.Schema({vol.Required('readings'):vol.All([dict],vol.Length(min=1,max=100))}),supports_response=SupportsResponse.OPTIONAL)
    async def get_readings(call: ServiceCall):
        return receiver.query(call.data)
    hass.services.async_register(DOMAIN,'get_readings',get_readings,schema=vol.Schema(QUERY_FIELDS),supports_response=SupportsResponse.ONLY)
    async def get_statistics(call: ServiceCall):
        return await receiver.statistics(call.data)
    hass.services.async_register(DOMAIN,'get_statistics',get_statistics,schema=vol.Schema({vol.Required('start'):cv.string,vol.Required('end'):cv.string}),supports_response=SupportsResponse.ONLY)
    websocket_api.async_register_command(hass,websocket_readings)
    await hass.http.async_register_static_paths([StaticPathConfig('/gas_photo/gas-photo-card.js',str(Path(__file__).parent/'static/gas-photo-card.js'),False)])
    await discovery.async_load_platform(hass,'sensor',DOMAIN,{},config)
    async_track_time_change(hass,receiver.publish,minute=0,second=5)
    await receiver.publish()
    return True
