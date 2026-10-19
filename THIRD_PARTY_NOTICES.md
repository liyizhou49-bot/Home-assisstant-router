# 第三方来源

本项目不把任何模型权重放进源码仓库，包括自编分类头和第三方 ONNX 文件。它们在运行时下载，仍适用上游许可证，不能用项目 MIT 许可证替代。

| 内容 | 来源与许可证 |
| --- | --- |
| BGE-small-zh-v1.5 | [BAAI 模型卡（MIT）](https://huggingface.co/BAAI/bge-small-zh-v1.5)；[Xenova ONNX 转换](https://huggingface.co/Xenova/bge-small-zh-v1.5)，固定修订 `75c43b069aac4d136ba6bc1122f995fedcfd2781` |
| Kokoro-82M-v1.1-zh | [Hexgrad 模型卡（Apache-2.0）](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh) |
| Kokoro ONNX 文件及运行库 | [thewh1teagle/kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx)，按其仓库许可证；模型文件来自 `model-files-v1.1` release |
| Misaki 中文前端 | [hexgrad/misaki（Apache-2.0）](https://github.com/hexgrad/misaki/blob/main/LICENSE)，包括其标注的 PaddleSpeech 改编部分；依赖安装保留上游许可文件 |
| 其他库 | NumPy、ONNX Runtime、tokenizers、Hassil、pypinyin、RapidFuzz、jieba、cn2an、pypinyin-dict 等由包管理器安装，分别遵守对应发行包中的许可 |
| 系统软件 | Debian、Python、espeak-ng、libsndfile 等由基础镜像/系统包安装，其许可及源码渠道按上游软件包说明保留；espeak-ng 的许可证见 [官方仓库](https://github.com/espeak-ng/espeak-ng) |

固定下载地址、完整 SHA256 和字节数见 `router_runtime/assets.py`。Kokoro 语音数据来源和模型训练说明见原始模型卡。本项目没有重新分发其原始训练音频或数据集。

语音识别另行安装官方 Home Assistant Whisper 应用，其支持的 Whisper、FunASR、SenseVoice 模型不包含在本项目中。SenseVoice 来源及使用条款见 [FunAudioLLM/SenseVoiceSmall 模型卡](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)，以下载时上游条款为准。
