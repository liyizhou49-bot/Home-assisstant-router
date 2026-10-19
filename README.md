# 家庭助手路由 · 0.8.3

Home Assistant 中文家庭助手：简单指令在本地处理，复杂表达交给可选大模型；语音可选本地 Kokoro / Piper 或千问云端。分类、纠错和本地语音计算都在 HA 主机内，不需要 Windows 后台程序。

## 界面和组成

顶部保留总览、对话、设置和房间。设置内分为四页：基本设置、模型管理、句式学习、生活习惯。路由正常/节流、空闲学习、服务商与 API、语音本地/云端、模型/音色、线程、自然/极速播放和提示词集中管理。习惯页提供备份创建与下载。

- `custom_components/router_lab`：自定义集成、对话代理、虚拟家电、习惯、统一设置。
- `custom_components/qwen_cloud_tts`：千问流式朗读；收到第一段音频前可回退到已配置的 Piper。
- `router_runtime`：独立计算应用，运行冻结分类器、纠错和 Kokoro。没有设备控制权，不持有 HA 或云端密钥，不映射主机端口。

## 安装

下载 [v0.8.3 测试版源码](https://github.com/liyizhou49-bot/Home-assisstant-router/archive/refs/tags/v0.8.3.zip)，解压后进入项目目录，再执行下面的安装命令。项目仍在测试阶段，请先在测试环境或完整备份后的 HA 上使用。

已实测环境：**HAOS 18.3、Core 2026.10.2、amd64、2 vCPU / 4 GB**。依赖新版 HA 子条目和语音接口，不承诺兼容旧版。aarch64 仅提供应用声明，未实机验证；HA Container 暂不支持此安装器。现有测试环境已完成升级验证，独立全新 HA 安装器还需验证。

安装 Python 3.12 以上，解压后运行：

```console
python -m pip install -r tools/requirements.txt
python tools/install.py --ha-url http://homeassistant.local
```

安装器要求输入 HA 长期令牌，输入不回显、不写文件。通过官方 File editor 和认证内部终端安装，不开放 SSH 端口。安装完打开“家庭助手 → 设置”，填写自己的 API。安装验证会产生少量用量。

1. 在“模型管理”选择下载中文路由模型，程序会从本项目 Release 下载冻结分类头，并从上游下载编码器；等待校验安装完成。若使用镜像或自建版本，可展开分类头下载地址手动填写。
2. 在“基本设置”选择大模型服务商，填写对应 API，读取模型列表并选择支持工具调用的模型，保存时验证两轮流式工具调用。
3. 语音选择云端时填写千问 API Host、API、模型和匹配音色；选择本地时下载 Kokoro 并保存，Piper 使用另行安装并配置的 Wyoming 中文引擎。
4. 到“对话”测试。语音识别引擎需另行安装；配置后可在对话页点击麦克风，使用语音 → 转文字 → 路由 → 控制 → 朗读。尚未启用唤醒词。

密钥输入框始终为空并显示是否已配置；留空保留已保存的值，不代表删除。不同地址的 API 分开存储，不会跨厂商沿用。失败时回滚本次修改，保留无关账号配置。用户习惯文件不保存 API。

作为自定义应用仓库安装时，计算应用主机名带仓库前缀，请把信息页主机名填入“设置 → 高级：虚拟机计算服务地址”。本地安装默认 `http://local-router-runtime:17861`。

## 大模型服务商

支持 DeepSeek、OpenAI GPT、Google Gemini、Moonshot Kimi 和自定义 OpenAI 兼容接口。API 模型列表来自自己的账号，非固定可用承诺。接口必须支持模型列表、Chat Completions、流式工具调用及工具结果回传。

DeepSeek 已在 HA 实际控制验证。其他服务商尚无实际密钥，完成接口适配与模拟检查，填入 API 后仍须通过保存验证。Gemini 工具签名保留在有界内存缓存中；不会写入习惯文件。Kimi 当前采用 K2.6 / K2.5 非思考模式，暂不支持强制思考的 K3、K2.7、thinking 型号。复杂推理、Responses-only 或非工具对话模型不保证兼容。

## 千问语音模型

| 模型 ID | 北京公有价参考 / 万字符 | 接口说明 |
| --- | ---: | --- |
| qwen3-tts-flash-realtime | 1 元 | 工作空间实时接口，已实测 |
| qwen-audio-3.0-tts-flash | 1 元 | 工作空间音频接口，已实测 |
| qwen-audio-3.0-tts-plus | 1.4 元 | 工作空间音频接口，已实测 |
| qwen3-tts-flash | 0.8 元 | 需传统 DashScope Host 与对应 API；当前工作空间不支持，未实测传统账号 |

价格核对日期 2026-10-18，工作空间优惠、地区和权限以控制台为准，官方链接在设置页和模型目录中。模型与音色联动；更换 Host 必须明确填写对应 API。实时版支持流式文本输入，其他型号等完整文本后合成。“极速”收到音频即播放，“自然”等完整音频。本地 Kokoro 按完整回复合成，首次加载耗时较高。

## 所有模型独立下载

**源码、容器镜像和源码发布压缩包均不含模型权重，包括本项目的分类头。** 第三方文件从 `router_runtime/assets.py` 的固定上游源下载，包含大小、SHA256 校验及原子替换。模型保存在应用持久化 `/data/models`，更新应用保留文件。网络失败可重试。

| 模型 | 文件占用（十进制） | 说明 |
| --- | ---: | --- |
| BGE 中文 INT8 | 约 24 MB | 编码器；还需要单独的冻结分类头 |
| Kokoro 中文 FP32 | 约 380 MB | 含共享音色，推荐 |
| Kokoro 中文 FP16 | 约 217 MB | 含共享音色；CPU 不保证更快 |

本地模型可以删除并重新下载；正在使用的语音模型先切换后才能删除。共享音色在另一个模型仍需要时保留。路由核心不提供运行中删除。默认 2 个 CPU 线程。

项目专用冻结分类头作为 [router-heads-v4.zip](https://github.com/liyizhou49-bot/Home-assisstant-router/releases/download/v0.8.3/router-heads-v4.zip) 单独提供，约 67 KB，不是训练集。源码中仅记录下载地址与校验值，BGE / Kokoro 等大模型从各自上游下载。默认下载地址已配置；如需手动设置，把上述附件链接填入“模型管理 → 项目专用分类头下载地址”。不要重新打包或改动附件，否则校验失败。

## 记忆与学习

习惯存在 HA `.storage/router_lab_habits.<配置条目ID>`，支持回家、离家、睡觉、起床及自定义场景；文本偏好与可执行规则分开，按 HA 账号隔离。可在设置中备份下载，HA 完整备份也应包含它。场景由对话触发，不会擅自生成定位或传感器自动化。

空闲学习使用当前大模型分析本地回退，有限规则通过本地证据与参数边界检查后启用；不是重新训练分类器，也不能赋予设备 HA 未提供的服务。发布包不含个人习惯、学习记录、原始 Excel、训练路径或样本文本哈希。保留测试集没有用于训练。

## 检查

```console
python -m pip install hassil==3.12.1 aiohttp==3.13.2
python -m unittest test_unified_settings test_providers test_tool_stream test_qwen_cloud_tts test_scene_memory test_habits test_route_learning test_pipeline test_habit_backup test_speech_input test_microphone_gateway test_recognition_timing test_trace_card
node test_settings_frontend.cjs
node test_voice_modes.cjs
node test_stt_modes.cjs
```

这些行为检查使用模拟 HA，不读取真实 API、不控制设备。源码采用 MIT，第三方依赖与下载模型遵守各自许可证，见 `THIRD_PARTY_NOTICES.md`。

## 上传 GitHub

**只上传此目录。** 外层工作目录含本地配置、备份、模型、测试记录和虚拟机文件，不要一起上传。发布包采用文件白名单生成，并附 `.gitignore`；忽略规则不能清除已提交的秘密。本项目目前不提供 HACS 一键安装；安装方式见上文。

## 电脑麦克风与本地中文识别

在 HA 安装官方 Whisper 应用，选择 `model: base-int8`、`language: zh`、`stt_library: faster-whisper`、`beam_size: 1`，启动后添加发现的 Wyoming 服务。在统一设置的“语音输入”选择已安装的识别引擎和中文，保存；对话页的“开始语音对话”直接使用原来的路由管线。模型由官方应用下载，不随本源码包分发。

浏览器录音需要安全上下文。已有 HTTPS 的 HA 可直接使用；本机开发环境可以在持有麦克风的电脑运行：

```console
python tools/microphone_gateway.py --ha-url http://自己的HA地址 --port 18123
```

然后打开 `http://localhost:18123/router-lab/voice`，登录 HA 并自行允许麦克风。这个进程只转发页面和音频，不运行模型、不保存录音、不注入凭证，只监听本机地址。它不会关闭 HA 认证或修改浏览器安全设置。关闭进程后入口失效；其他电脑不能借用这台电脑的 localhost。

语音页和设置页均有“语音识别：正常 / 极速”按钮，与朗读的“自然 / 极速”分开。正常使用官方 Whisper 应用内的 FunASR / SenseVoice 中文模型（`model: auto`、`stt_library: funasr`）；极速使用 Whisper Base INT8 / beam 1。按钮仅支持当前助手使用官方 `stt.faster_whisper` 引擎、HA 管理员账号及支持这些选项的官方应用版本，其他引擎仍从统一设置选择。切换会保存应用配置并重启识别应用，无需重启 HA；下载或加载模型需要等待。切换失败会尝试恢复原配置，检测到其他页面已更改配置时不会覆盖。两个模式均使用中性的中文提示，不写入固定问题答案或家电名词表。

早期相同 8 条生成录音比较中，Whisper Small / beam 5 与 Base / beam 1 的平均转写响应约 2.96 秒与 0.93 秒，完整文字匹配均为 6/8。随后发现 Base 和 Small 都会把“激光三要素”听错，正常档因此改用 SenseVoice。在新的 4 条生成录音上，SenseVoice 全部完整转写，响应约 2.7～4.7 秒；Base 约 0.9 秒，但只有 1 条完整转写。随后真人麦克风复测已正确识别“激光三要素是啥？”，HA 检测语音结束到文字返回约 0.72 秒。小样本不能代表其他句子、口音和硬件；没有用这些句子训练模型。以上计时为完整录音提交后到文字返回，不含实际说话、结束检测、LLM 或朗读。

## 能看见的识别耗时

“最近一次测试”卡及对话页显示本次语音识别模式和转写等待。语音识别耗时使用同一次原生 Assist 请求的 `stt-vad-end → stt-end` 时间；录音到文字使用 `stt-start → stt-end`，包含开始后的等待、说话和结束检测。“文字处理”沿用路由接收文字后的耗时，两者不混为一项。手动结束录音、引擎未上报结束时间时，识别等待显示未记录；文字输入不会复用上一条语音时间。当前适配 HA 2026.10.2 的运行元数据，读取失败时只缺少计时，不影响正常对话，不录存音频。

节流模式筛选相关设备能力时会参考最近两轮对话，避免“调到28度吧”省略空调名称后只选到灯。当前明确点名的设备优先；能力列表仍有数量上限，不增加模型训练。已验证同一对话内追问空调温度后回答“调到28度吧”能完成虚拟空调设置，测试后恢复其原状态。
