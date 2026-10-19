DOMAIN = 'qwen_cloud_tts'
MODEL = 'qwen3-tts-flash-realtime'
VOICES = {'Cherry': '芊悦', 'Serena': '苏瑶', 'Maia': '四月', 'Chelsie': '千雪',
          'Ethan': '晨煦', 'Moon': '月白'}
MODELS={
    'qwen3-tts-flash-realtime':{'name':'千问3 Flash Realtime · 边输入边合成','protocol':'realtime','voices':VOICES,'price_cny_per_10k':1.0,
        'source':'https://help.aliyun.com/zh/model-studio/qwen3-tts-flash-realtime'},
    'qwen3-tts-flash':{'name':'千问3 Flash · 需要传统 DashScope 接口','protocol':'qwen_http','legacy_only':True,'voices':VOICES,'price_cny_per_10k':0.8,
        'source':'https://help.aliyun.com/zh/model-studio/qwen3-tts-flash'},
    'qwen-audio-3.0-tts-flash':{'name':'千问 Audio 3.0 Flash · 快速合成','protocol':'audio_http',
        'voices':{'longanfengyue':'龙安风悦','longanhuan_v3.6':'龙安欢','longanlingxi':'龙安灵希'},'price_cny_per_10k':1.0,
        'source':'https://help.aliyun.com/zh/model-studio/qwen-audio-3-0-tts-flash'},
    'qwen-audio-3.0-tts-plus':{'name':'千问 Audio 3.0 Plus · 高品质','protocol':'audio_http',
        'voices':{'longanlingxin':'龙安灵心','longanlufeng':'龙安鲁风'},'price_cny_per_10k':1.4,
        'source':'https://help.aliyun.com/zh/model-studio/qwen-audio-3-0-tts-plus'},
}
PRICE_CHECKED='2026-10-18'
