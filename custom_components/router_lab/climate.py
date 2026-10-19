from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from .const import DOMAIN
from .entity import LabDevice
from .catalog import CLIMATES
from .const import SIGNAL_ENVIRONMENT
from homeassistant.helpers.dispatcher import async_dispatcher_connect


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    if runtime['settings'].get('virtual_devices', True):
        devices = [LabClimate(entry,key,name) for key,name,_room in CLIMATES]
        runtime['devices'].extend(devices)
        async_add_entities(devices)


class LabClimate(LabDevice, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.AUTO]
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE |
                                ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF)
    _attr_min_temp = 16
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5

    def __init__(self, entry, key='air_conditioner', name='客厅空调'):
        self.setup_device(entry, key, name, 'climate')
        self.entry_id = entry.entry_id
        self.environment_key = 'bedroom_temperature' if key == 'bedroom_ac' else 'temperature'
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_current_temperature = 27.0
        self._attr_target_temperature = 26.0

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass,SIGNAL_ENVIRONMENT,self.async_write_ha_state))

    @property
    def current_temperature(self):
        return self.hass.data[DOMAIN][self.entry_id]['environment'][self.environment_key]

    @property
    def hvac_action(self):
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self.hvac_mode == HVACMode.COOL and self.current_temperature > self.target_temperature:
            return HVACAction.COOLING
        if self.hvac_mode == HVACMode.HEAT and self.current_temperature < self.target_temperature:
            return HVACAction.HEATING
        return HVACAction.IDLE

    async def async_set_temperature(self, **kwargs):
        value = float(kwargs[ATTR_TEMPERATURE])
        if not 16 <= value <= 30:
            raise ValueError('虚拟空调温度范围为16至30度')
        self._attr_target_temperature = value
        if 'hvac_mode' in kwargs:
            self._attr_hvac_mode = HVACMode(kwargs['hvac_mode'])
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        self._attr_hvac_mode = HVACMode(hvac_mode)
        self.async_write_ha_state()

    async def async_turn_on(self):
        await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_turn_off(self):
        await self.async_set_hvac_mode(HVACMode.OFF)

    def reset(self):
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = 26.0
        self.async_write_ha_state()
