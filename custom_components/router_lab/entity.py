from homeassistant.helpers.entity import Entity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN


class LabDevice(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = False

    def setup_device(self, entry, key, name, platform):
        self._attr_unique_id = f'{entry.entry_id}_{key}'
        self._attr_name = name
        self.entity_id = f'{platform}.router_lab_{key}'
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name='虚拟家庭测试台',
            manufacturer='本地测试', model='虚拟设备，无真实硬件')

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
        async_expose_entity(self.hass, 'conversation', self.entity_id, True)

    @property
    def extra_state_attributes(self):
        return {'router_lab_virtual': True}
