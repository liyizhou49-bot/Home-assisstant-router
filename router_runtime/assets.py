"""Allowlisted, hash-verified model sources. No arbitrary download URLs."""
BGE_BASE = 'https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/75c43b069aac4d136ba6bc1122f995fedcfd2781/'
KOKORO_BASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/'
HEAD_FILES = {'binary_v1/metadata.json': '6ef3ef7b07b1f0762fb001f2e06ca75e314c5c3728a41a70a0945e7401a3d140', 'binary_v1/weights.npz': '9399615051b7d8126db87a79ee106288280539fb053da300da0db1aaa8b6d686', 'hybrid_v4/metadata.json': '912768306d470f6564f91fa0e39e914e6961e10b2cad16bb4018d638797e1ddb', 'hybrid_v4/weights.npz': '85f90c38fc2090a476e45ebbff65de3d82f3cf182235e0554c551dc4674f4927'}
ASSETS = {
    'heads': ('router-heads-v4.zip', 'https://github.com/liyizhou49-bot/Home-assisstant-router/releases/download/v0.8.3/router-heads-v4.zip', 'a9f0068a0914ae62ce1676ecf8690f2b1838c0151ff0424d008be5ddba46847a', 67333),
    'bge_model': ('encoder_bge_zh/model_int8.onnx', BGE_BASE+'onnx/model_int8.onnx', 'b9837c19ce154ff0726d398ee77abbc03a7faf0476c6f93016c84e531be7ebb5', 23903394),
    'bge_tokenizer': ('encoder_bge_zh/tokenizer.json', BGE_BASE+'tokenizer.json', '48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26', 439125),
    'fp32': ('kokoro/kokoro-v1.1-zh.onnx', KOKORO_BASE+'kokoro-v1.1-zh.onnx', '859f9ded9f53be16c24857cdab3254a45da53c3afd5ba6ef134c7de3f822e326',325506167),
    'fp16': ('kokoro/kokoro-v1.1-zh.fp16.onnx', KOKORO_BASE+'kokoro-v1.1-zh.fp16.onnx','a628ea5d6fbde96d1a85f691a6a00847829937f9e488021ba2c5359bc6ea08b5',163528759),
    'voices': ('kokoro/voices-v1.1-zh.bin',KOKORO_BASE+'voices-v1.1-zh.bin','14cb6186c99e4f6016871405f62046c5df863ae27465cbdc4ee08be7dd703acd',53815880),
    'config': ('kokoro/config.json','https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/resolve/main/config.json','bc333efa5ce4ceff433c8c8e5d027a1eca0166001e4e4a62bea2d26ff7a46890',3228),
}
MODELS = {
    'router': {'name':'中文语义路由 BGE INT8','assets':['heads','bge_model','bge_tokenizer'],'kind':'router'},
    'kokoro_fp32': {'name':'Kokoro 中文 FP32 · 推荐','assets':['fp32','voices','config'],'kind':'tts','note':'约 380 MB；音质优先，CPU 默认使用 2 个线程。'},
    'kokoro_fp16': {'name':'Kokoro 中文 FP16','assets':['fp16','voices','config'],'kind':'tts','note':'约 217 MB；减小磁盘占用，在 CPU 上不保证更快。'},
}
VOICES = ['zf_001','zf_003','zf_017','zf_094','zm_010','zm_031','zm_069','zm_089']
