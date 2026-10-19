"""Virtual home inventory, independent from routing and training."""
LIGHTS = [
    ('living_room','客厅灯','客厅'), ('bedroom','卧室灯','卧室'), ('entryway','玄关灯','玄关'),
    ('kitchen','厨房灯','厨房'), ('dining','餐厅灯','餐厅'), ('study','书房灯','书房'),
    ('bathroom','卫生间灯','卫生间'), ('balcony','阳台灯','阳台'),
    ('bedside','床头灯','卧室'), ('guest','客卧灯','客卧'),
]
CLIMATES = [('air_conditioner','客厅空调','客厅'), ('bedroom_ac','卧室空调','卧室'), ('study_ac','书房空调','书房')]
COVERS = [
    ('curtain','客厅窗帘','客厅','curtain'), ('bedroom_curtain','卧室窗帘','卧室','curtain'),
    ('study_curtain','书房窗帘','书房','curtain'),
    ('standing_desk','升降桌','书房',None), ('adjustable_bed','智能床','卧室',None),
]
FANS = [('living_fan','客厅风扇','客厅'), ('bedroom_fan','卧室风扇','卧室'), ('purifier','空气净化器','客厅')]
SWITCHES = [
    ('kettle','电热水壶','厨房','mdi:kettle',None),
    ('water_heater','热水器','卫生间','mdi:water-boiler',None),
    ('socket','智能插座','客厅','mdi:power-socket-cn',None),
    ('washer','洗衣机','阳台','mdi:washing-machine',None),
    ('dishwasher','洗碗机','厨房','mdi:dishwasher',None),
    ('dryer','烘干机','阳台','mdi:tumble-dryer',None),
    ('coffee','咖啡机','厨房','mdi:coffee-maker',None),
    ('hood','油烟机','厨房','mdi:stove',None),
    ('fridge','冰箱电源','厨房','mdi:fridge',None),
    ('simulate_door','模拟开门','玄关','mdi:door','door'),
    ('simulate_presence','模拟有人','客厅','mdi:account','presence'),
    ('simulate_leak','模拟漏水','卫生间','mdi:water-alert','leak'),
    ('simulate_smoke','模拟烟雾','厨房','mdi:smoke-detector','smoke'),
]
MEDIA = [('television','客厅电视','客厅','tv'), ('speaker','客厅音箱','客厅','speaker')]
HUMIDIFIERS = [('humidifier','卧室加湿器','卧室')]
SENSORS = [
    ('temperature','客厅温度','客厅','temperature','temperature','°C'),
    ('humidity','客厅湿度','客厅','humidity','humidity','%'),
    ('illuminance','客厅照度','客厅','illuminance','illuminance','lx'),
    ('pm25','客厅PM2.5','客厅','pm25','pm25','µg/m³'),
    ('bedroom_temperature','卧室温度','卧室','bedroom_temperature','temperature','°C'),
    ('bathroom_humidity','卫生间湿度','卫生间','bathroom_humidity','humidity','%'),
]
BINARY_SENSORS = [
    ('door','大门状态','玄关','door','door'), ('presence','客厅有人','客厅','presence','occupancy'),
    ('leak','卫生间漏水','卫生间','leak','moisture'), ('smoke','厨房烟雾','厨房','smoke','smoke'),
]
NUMBERS = [
    ('simulate_temperature','模拟室温','客厅','temperature',10,40,.5,'°C'),
    ('simulate_humidity','模拟湿度','客厅','humidity',0,100,1,'%'),
    ('simulate_illuminance','模拟照度','客厅','illuminance',0,2000,10,'lx'),
    ('simulate_pm25','模拟PM2.5','客厅','pm25',0,500,1,'µg/m³'),
]
ENVIRONMENT_DEFAULTS = {'temperature':27.0,'humidity':45.0,'illuminance':350.0,'pm25':12.0,
                        'bedroom_temperature':26.0,'bathroom_humidity':60.0,
                        'door':False,'presence':False,'leak':False,'smoke':False}


def inventory():
    for platform, items in [('light',LIGHTS),('climate',CLIMATES),('cover',COVERS),('fan',FANS),
                           ('switch',SWITCHES),('media_player',MEDIA),('humidifier',HUMIDIFIERS),
                           ('sensor',SENSORS),('binary_sensor',BINARY_SENSORS),('number',NUMBERS)]:
        for key,name,room,*_ in items:
            yield (f'{platform}.router_lab_{key}',name,room)
