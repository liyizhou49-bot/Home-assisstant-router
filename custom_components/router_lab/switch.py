from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.dispatcher import async_dispatcher_send
from .const import DOMAIN,SIGNAL_ENVIRONMENT
from .entity import LabDevice
from .catalog import SWITCHES


async def async_setup_entry(hass,entry,async_add_entities):
    runtime=hass.data[DOMAIN][entry.entry_id]
    if runtime['settings'].get('virtual_devices',True):
        devices=[LabSwitch(entry,key,name,icon,environment_key) for key,name,_room,icon,environment_key in SWITCHES]
        runtime['devices'].extend(devices)
        async_add_entities(devices)


class LabSwitch(LabDevice,SwitchEntity):
    def __init__(self,entry,key,name,icon,environment_key):
        self.setup_device(entry,key,name,'switch')
        self.entry_id=entry.entry_id
        self.environment_key=environment_key
        self._attr_icon=icon
        self._attr_is_on=False

    def set_value(self,value):
        self._attr_is_on=bool(value)
        if self.environment_key:
            self.hass.data[DOMAIN][self.entry_id]['environment'][self.environment_key]=bool(value)
            async_dispatcher_send(self.hass,SIGNAL_ENVIRONMENT)
        self.async_write_ha_state()

    async def async_turn_on(self,**kwargs):
        self.set_value(True)

    async def async_turn_off(self,**kwargs):
        self.set_value(False)

    def reset(self):
        self.set_value(False)
