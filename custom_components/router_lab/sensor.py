from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.components.sensor import SensorDeviceClass,SensorStateClass
from .const import DOMAIN, SIGNAL_TRACE,SIGNAL_ENVIRONMENT
from .entity import LabDevice
from .catalog import SENSORS


async def async_setup_entry(hass, entry, async_add_entities):
    entities=[TraceSensor(entry),MemorySensor(entry),LearningSensor(entry)]
    if hass.data[DOMAIN][entry.entry_id]['settings'].get('virtual_devices',True):
        entities.extend(LabSensor(entry,key,name,env,kind,unit) for key,name,_room,env,kind,unit in SENSORS)
    async_add_entities(entities)


class LearningSensor(SensorEntity):
    _attr_name='路由模式与本地扩展'
    _attr_should_poll=False
    _attr_icon='mdi:school-outline'
    def __init__(self,entry):
        self.entry=entry;self._attr_unique_id=f'{entry.entry_id}_learning'
        self.entity_id='sensor.router_lab_learning'
    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass,SIGNAL_TRACE,self.async_write_ha_state))
    @property
    def native_value(self):
        learning=self.hass.data[DOMAIN][self.entry.entry_id]['learning']
        return '节流' if learning.setting('routing_mode','normal')=='economy' else '正常'
    @property
    def extra_state_attributes(self):
        learning=self.hass.data[DOMAIN][self.entry.entry_id]['learning']
        return {'status':learning.status,'rule_count':sum(r['enabled'] for r in learning.data['rules']),
                'pending_count':sum(c['status']=='pending' for c in learning.data['cases']),
                'daily_limit':learning.setting('learning_daily_limit',3),'usage':dict(learning.usage()),
                'idle_learning':learning.setting('idle_learning',True)}


class LabSensor(LabDevice,SensorEntity):
    _attr_state_class=SensorStateClass.MEASUREMENT

    def __init__(self,entry,key,name,environment_key,kind,unit):
        self.setup_device(entry,key,name,'sensor')
        self.entry_id=entry.entry_id
        self.environment_key=environment_key
        self._attr_device_class=SensorDeviceClass(kind)
        self._attr_native_unit_of_measurement=unit

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass,SIGNAL_ENVIRONMENT,self.async_write_ha_state))

    @property
    def native_value(self):
        return self.hass.data[DOMAIN][self.entry_id]['environment'][self.environment_key]


class TraceSensor(SensorEntity):
    _attr_name = '最近一次路由测试'
    _attr_should_poll = False
    _attr_icon = 'mdi:routes'

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f'{entry.entry_id}_trace'
        self.entity_id = 'sensor.router_lab_trace'

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_TRACE, self.async_write_ha_state))

    @property
    def native_value(self):
        history = self.hass.data[DOMAIN][self.entry.entry_id]['history']
        return history[-1]['final_route'] if history else '等待输入'

    @property
    def extra_state_attributes(self):
        history = self.hass.data[DOMAIN][self.entry.entry_id]['history']
        return dict(history[-1]) if history else {}


class MemorySensor(SensorEntity):
    _attr_name='已记住的生活习惯'
    _attr_should_poll=False
    _attr_icon='mdi:brain'

    def __init__(self,entry):
        self.entry=entry
        self._attr_unique_id=f'{entry.entry_id}_memory'
        self.entity_id='sensor.router_lab_memory'

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass,SIGNAL_TRACE,self.async_write_ha_state))

    @property
    def native_value(self):
        profiles=self.hass.data[DOMAIN][self.entry.entry_id]['memory'].data['profiles'].values()
        return sum(len(profile['rules']) for profile in profiles)

    @property
    def extra_state_attributes(self):
        memory=self.hass.data[DOMAIN][self.entry.entry_id]['memory']
        profiles=list(memory.data['profiles'].values())
        date=memory.environment()['date']
        now=memory.environment()['time']
        return {'revision':memory.data['revision'],'temporary_count':sum(sum(r['date']==date and (not r.get('expires_at') or r['expires_at']>now) for r in p['temporary']) for p in profiles),
                'note_count':sum(len(p['notes']) for p in profiles),'suggestion_count':sum(len(p['suggestions']) for p in profiles),
                'last_backup':memory.backup_index.get('last_created_at'),
                'backup_count':len(memory.backup_index['backups']),
                'storage':'HA本地持久保存','scope':'按HA用户分别保存；无登录用户时为家庭共享身份'}
