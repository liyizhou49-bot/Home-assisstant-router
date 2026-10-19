from homeassistant.components.media_player import MediaPlayerEntity,MediaPlayerEntityFeature,MediaPlayerState,MediaPlayerDeviceClass
from .const import DOMAIN
from .entity import LabDevice
from .catalog import MEDIA


async def async_setup_entry(hass,entry,async_add_entities):
    runtime=hass.data[DOMAIN][entry.entry_id]
    if runtime['settings'].get('virtual_devices',True):
        devices=[LabPlayer(entry,key,name,kind) for key,name,_room,kind in MEDIA]
        runtime['devices'].extend(devices)
        async_add_entities(devices)


class LabPlayer(LabDevice,MediaPlayerEntity):
    _attr_supported_features=(MediaPlayerEntityFeature.TURN_ON|MediaPlayerEntityFeature.TURN_OFF|
        MediaPlayerEntityFeature.PLAY|MediaPlayerEntityFeature.PAUSE|MediaPlayerEntityFeature.STOP|
        MediaPlayerEntityFeature.VOLUME_SET|MediaPlayerEntityFeature.VOLUME_MUTE)

    def __init__(self,entry,key,name,kind):
        self.setup_device(entry,key,name,'media_player')
        self._attr_device_class=MediaPlayerDeviceClass(kind)
        self._attr_state=MediaPlayerState.OFF
        self._attr_volume_level=.3
        self._attr_is_volume_muted=False

    async def async_turn_on(self):
        self._attr_state=MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_turn_off(self):
        self._attr_state=MediaPlayerState.OFF
        self.async_write_ha_state()

    async def async_media_play(self):
        self._attr_state=MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_media_pause(self):
        self._attr_state=MediaPlayerState.PAUSED
        self.async_write_ha_state()

    async def async_media_stop(self):
        self._attr_state=MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_set_volume_level(self,volume):
        if not 0 <= volume <= 1:
            raise ValueError('音量范围为0至1')
        self._attr_volume_level=volume
        self.async_write_ha_state()

    async def async_mute_volume(self,mute):
        self._attr_is_volume_muted=bool(mute)
        self.async_write_ha_state()

    def reset(self):
        self._attr_state=MediaPlayerState.OFF
        self._attr_volume_level=.3
        self._attr_is_volume_muted=False
        self.async_write_ha_state()
