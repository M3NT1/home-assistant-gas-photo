"""Latest observations; deliberately no state_class or generated sum."""
from datetime import datetime
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from . import DOMAIN

async def async_setup_platform(hass,config,async_add_entities,discovery_info=None):
    if discovery_info is not None:
        async_add_entities([
            GasPhotoSensor(hass,False),
            GasPhotoSensor(hass,True),
            GasPhotoPublicationStatusSensor(hass),
        ])

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


class GasPhotoPublicationStatusSensor(SensorEntity):
    """Sensor tracking the committed publication status and daily coverage snapshot."""
    _attr_should_poll = False

    def __init__(self, hass):
        self.hass = hass
        self._attr_unique_id = 'gas_photo_publication_status'
        self._attr_name = 'Gas photo publication status'
        self.entity_id = 'sensor.gas_photo_publication_status'

    @property
    def native_value(self):
        receiver = self.hass.data.get(DOMAIN)
        if not receiver:
            return "unknown"
        data = receiver.ledger.data
        if data.get('rebuild_in_progress'):
            return "rebuilding"
        if data.get('pending'):
            return "pending"
        return "synchronized"

    @property
    def extra_state_attributes(self):
        receiver = self.hass.data.get(DOMAIN)
        if not receiver:
            return {}
        data = receiver.ledger.data
        return {
            "published_revision_id": data.get("published_revision_id", 0),
            "published_daily_coverage": data.get("published_daily_coverage", {}),
        }

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, DOMAIN + '_updated', self.async_write_ha_state))
        self.async_on_remove(async_dispatcher_connect(self.hass, DOMAIN + '_published', self.async_write_ha_state))
