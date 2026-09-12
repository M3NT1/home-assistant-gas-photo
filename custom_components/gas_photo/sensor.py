"""Latest observations; deliberately no state_class or generated sum."""
from datetime import datetime
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from . import DOMAIN

async def async_setup_platform(hass,config,async_add_entities,discovery_info=None):
    if discovery_info is not None:
        async_add_entities([GasPhotoSensor(hass,False),GasPhotoSensor(hass,True)])

class GasPhotoSensor(SensorEntity):
    _attr_should_poll=False
    def __init__(self,hass,is_time):
        self.hass=hass
        self.is_time=is_time
        self._attr_unique_id='gas_photo_gas_main_'+('captured_at' if is_time else 'reading')
        self._attr_name='Gas photo '+('last capture' if is_time else 'latest reading')
        self._attr_device_class=SensorDeviceClass.TIMESTAMP if is_time else SensorDeviceClass.GAS
        if not is_time: self._attr_native_unit_of_measurement='m³'

    @property
    def native_value(self):
        rows=self.hass.data[DOMAIN].ledger._all()
        if not rows:return None
        row=rows[-1]
        return datetime.fromisoformat(row['captured_at']) if self.is_time else float(row['value'])

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass,DOMAIN+'_updated',self.async_write_ha_state))
