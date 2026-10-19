from homeassistant.components.binary_sensor import BinarySensorEntity,BinarySensorDeviceClass
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN,SIGNAL_ENVIRONMENT
from .entity import LabDevice
from .catalog import BINARY_SENSORS


async def async_setup_entry(hass,entry,async_add_entities):
    if hass.data[DOMAIN][entry.entry_id]['settings'].get('virtual_devices',True):
        async_add_entities([LabBinary(entry,key,name,env,kind) for key,name,_room,env,kind in BINARY_SENSORS])


class LabBinary(LabDevice,BinarySensorEntity):
    def __init__(self,entry,key,name,environment_key,kind):
        self.setup_device(entry,key,name,'binary_sensor')
        self.entry_id=entry.entry_id
        self.environment_key=environment_key
        self._attr_device_class=BinarySensorDeviceClass(kind)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass,SIGNAL_ENVIRONMENT,self.async_write_ha_state))

    @property
    def is_on(self):
        return self.hass.data[DOMAIN][self.entry_id]['environment'][self.environment_key]
