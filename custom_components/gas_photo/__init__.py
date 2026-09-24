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
METADATA_ESTIMATED = {'statistic_id':'gas_photo:gas_estimated','source':DOMAIN,'name':'Gas photo · interpolated consumption','unit_of_measurement':'m³','unit_class':'volume','mean_type':StatisticMeanType.NONE,'has_sum':True}

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
        rows_main=candidate.prepare_publication()
        rows_est=candidate.estimated_hourly()
        if not rows_main and not rows_est:
            return 'pending' if candidate.data.get('pending') else 'idle'

        candidate.data['pending']=True
        await self.store.async_save(candidate.dump())
        self.ledger=candidate
        try:
            for offset in range(0,len(rows_main),500):
                batch=[{**r,'start':timestamp(r['start'])} for r in rows_main[offset:offset+500]]
                async_add_external_statistics(self.hass,METADATA,batch)
            for offset in range(0,len(rows_est),500):
                batch=[{**r,'start':timestamp(r['start'])} for r in rows_est[offset:offset+500]]
                async_add_external_statistics(self.hass,METADATA_ESTIMATED,batch)
            # Await database commit confirmation from recorder
            rec=get_instance(self.hass)
            if hasattr(rec,'async_block_till_done'):
                await rec.async_block_till_done()
            elif hasattr(rec,'block_till_done'):
                await self.hass.async_add_executor_job(rec.block_till_done)
            elif hasattr(rec,'async_recorder_block_till_done'):
                await rec.async_recorder_block_till_done()
            else:
                raise RuntimeError("Recorder does not support commit synchronization")
            # Update committed publication state snapshot ONLY AFTER confirmed DB commit
            candidate_committed=Ledger(self.ledger.dump(),self.ledger.max_rate)
            candidate_committed.data['published_revision_id']=candidate_committed.data.get('revision_id',1)
            candidate_committed.data['published_daily_coverage']=candidate_committed.daily_coverage()
            candidate_committed.data['pending']=False
            await self.store.async_save(candidate_committed.dump())
            self.ledger=candidate_committed
            async_dispatcher_send(self.hass,DOMAIN+'_published')
        except Exception:
            _LOGGER.exception('Statistics queue or commit failed; exact ledger remains pending, retry at next hour/startup')
            return 'pending'
        return 'queued'

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

    async def meter_replacement(self,call):
        user=await self.hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        if user is None or not user.is_admin: raise Unauthorized()
        async with self.lock:
            candidate=Ledger(self.ledger.dump(),self.ledger.max_rate)
            try:
                candidate.add_meter_replacement(
                    old_final_reading=str(call.data['old_final_reading']),
                    new_initial_reading=str(call.data['new_initial_reading']),
                    replacement_time=str(call.data['replacement_time']),
                )
            except ValueError as exc: raise ServiceValidationError(str(exc)) from exc
            await self.store.async_save(candidate.dump())
            self.ledger=candidate
            async_dispatcher_send(self.hass,DOMAIN+'_updated')
            status=await self._publish_locked()
        return {'status':'accepted','statistics_status':status}

    async def rebuild_statistics(self,call=None):
        if call:
            user=await self.hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
            if user is None or not user.is_admin: raise Unauthorized()
        async with self.lock:
            candidate=Ledger(self.ledger.dump(),self.ledger.max_rate)
            candidate.data['rebuild_in_progress']=True
            candidate.data['pending']=True
            await self.store.async_save(candidate.dump())
            self.ledger=candidate

            rec=get_instance(self.hass)
            cleared=False
            if rec is not None:
                if hasattr(rec,'async_clear_statistics'):
                    done_event=asyncio.Event()
                    def on_done():
                        self.hass.loop.call_soon_threadsafe(done_event.set)
                    try:
                        rec.async_clear_statistics(['gas_photo:gas_estimated'],on_done=on_done)
                        async with asyncio.timeout(10):
                            await done_event.wait()
                        cleared=True
                    except (TimeoutError,Exception) as exc:
                        _LOGGER.error('async_clear_statistics failed: %s',exc)
                elif hasattr(rec,'clear_statistics'):
                    try:
                        await self.hass.async_add_executor_job(rec.clear_statistics,['gas_photo:gas_estimated'])
                        cleared=True
                    except Exception as exc:
                        _LOGGER.error('clear_statistics failed: %s',exc)

            if not cleared:
                raise ServiceValidationError('Recorder clear_statistics failed or unsupported; rebuild aborted to preserve state')

            all_est_hours=self.ledger.estimated_hourly()
            for offset in range(0,len(all_est_hours),500):
                batch=[{**r,'start':timestamp(r['start'])} for r in all_est_hours[offset:offset+500]]
                async_add_external_statistics(self.hass,METADATA_ESTIMATED,batch)

            if hasattr(rec,'async_block_till_done'):
                await rec.async_block_till_done()
            elif hasattr(rec,'block_till_done'):
                await self.hass.async_add_executor_job(rec.block_till_done)
            elif hasattr(rec,'async_recorder_block_till_done'):
                await rec.async_recorder_block_till_done()
            else:
                raise ServiceValidationError("Recorder commit synchronization failed")

            candidate_done=Ledger(self.ledger.dump(),self.ledger.max_rate)
            candidate_done.data['rebuild_in_progress']=False
            candidate_done.data['published_revision_id']=candidate_done.data.get('revision_id',1)
            candidate_done.data['published_daily_coverage']=candidate_done.daily_coverage()
            candidate_done.data['pending']=False
            await self.store.async_save(candidate_done.dump())
            self.ledger=candidate_done
            async_dispatcher_send(self.hass,DOMAIN+'_published')
            return {'status':'rebuilt','hours_count':len(all_est_hours)}

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
    hass.services.async_register(DOMAIN,'rebuild_statistics',receiver.rebuild_statistics,supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN,'meter_replacement',receiver.meter_replacement,schema=vol.Schema({vol.Required('old_final_reading'):cv.string,vol.Required('new_initial_reading'):cv.string,vol.Required('replacement_time'):cv.string}),supports_response=SupportsResponse.OPTIONAL)
    websocket_api.async_register_command(hass,websocket_readings)
    await hass.http.async_register_static_paths([StaticPathConfig('/gas_photo/gas-photo-card.js',str(Path(__file__).parent/'static/gas-photo-card.js'),False)])
    await discovery.async_load_platform(hass,'sensor',DOMAIN,{},config)
    async_track_time_change(hass,receiver.publish,minute=0,second=5)
    if ledger.data.get('rebuild_in_progress'):
        _LOGGER.warning('Incomplete rebuild detected on startup; resuming rebuild')
        hass.async_create_task(receiver.rebuild_statistics())
    await receiver.publish()
    return True
