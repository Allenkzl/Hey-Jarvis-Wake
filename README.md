# hey-jarvis-wake

离线唤醒词监听服务：听到 **"Hey Jarvis"** 后播放本地应答音频（默认"在呢"）。
基于 [openWakeWord](https://github.com/dscripka/openWakeWord) 官方预训练模型（ONNX 推理），
**完全本地离线**、零云端依赖、单进程常驻。

> 从展厅 Reachy Mini 机器人实战部署中抽取的通用版本：设备/模型/应答/检测参数全部可配置，
> 换一台 Linux 设备（树莓派、ReSpeaker、普通 PC + USB 麦克风）即可快速迁移。

## 特性

- 🎯 本地推理：openWakeWord + onnxruntime，树莓派 5 上单帧推理 ~25ms，CPU 占用低
- 🎛️ 全配置化：音频设备自动探测（可覆盖）、任意唤醒词模型目录、应答音频可换可随机多选
- 🛡️ 抗误报：连续 N 帧（默认 3 帧 = 240ms）超过阈值才唤醒，过滤噪声尖峰
- 🔁 防回声：应答播放后重开采集流，避免自己播放的声音二次触发
- 📊 校准工具：提示音同步录音 → 自动分析真实发音得分 → 给出阈值建议
- 🧪 自测模式：`--test-wav` 用正/负样本验证模型与代码（无需真人发声）
- 💚 树莓派/Reachy Mini 实战验证（详见文末）

## 目录结构

```
hey-jarvis-wake/
├── wake_service.py          # 主程序（唯一运行入口）
├── config.example.json      # 可选 JSON 配置示例
├── requirements.txt
├── scripts/
│   ├── download_models.sh   # 下载官方 ONNX 模型 + SHA256 校验
│   └── install_service.sh   # 一键安装为 systemd 服务
├── tools/calibrate.py       # 阈值校准工具
├── deploy/                  # systemd 单元模板（由 install_service.sh 使用）
├── models/                  # 模型目录（.onnx 不入库，见 scripts/download_models.sh）
├── assets/                  # 应答音频（16kHz 立体声 16bit WAV）
└── tests/                   # 正/负样本 WAV（--test-wav 自测用）
```

## 快速开始（Linux）

前置：Python 3.10+、`alsa-utils`（提供 arecord/aplay）、麦克风 + 扬声器。

```bash
# 1. 独立虚拟环境
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 下载唤醒词模型（hey_jarvis，含 SHA256 校验）
./scripts/download_models.sh

# 3. 替换/新增应答音频（可选）：assets/*.wav
#    要求：非空、16kHz、立体声、16bit；多个文件时随机播一个

# 4. 试运行（前台，Ctrl+C 退出）
.venv/bin/python wake_service.py
```

日志出现 `READY` 后即可测试：安静环境下说 **"Hey Jarvis"**，应听到应答音频；每 30 秒会打印 `LISTENING peak_score=...` 心跳便于观察环境噪声水平。

## 配置

优先级：**命令行 > 环境变量（WAKE_\*）> JSON 配置文件 > 内置默认**。

| 配置项 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| 输入设备 | `WAKE_INPUT_DEVICE` | `auto` | `auto` 按 `reachymini_audio_src` → `default` → `plughw:0,0` 探测；ReSpeaker/普通 USB 麦可用 `plughw:1,0`（用 `arecord -l` 查卡号） |
| 输出设备 | `WAKE_OUTPUT_DEVICE` | `auto` | 同上，播放用 `aplay -l` 查 |
| 模型目录 | `WAKE_MODEL_DIR` | `models` | 任意 openWakeWord ONNX 目录；非 `melspectrogram/embedding_model` 的 `.onnx` 视为唤醒词分类器 |
| 应答目录 | `WAKE_RESPONSES_DIR` | `assets` | 应答 WAV 目录 |
| 声道 | `WAKE_CHANNEL` | `0` | `0`/`1` 选声道，`mean` 取平均 |
| 阈值 | `WAKE_THRESHOLD` | `0.2` | 分数范围 0~1，越低越灵敏 |
| 连续帧 | `WAKE_HITS` | `3` | 连续 N 帧（80ms/帧）超阈值才唤醒 |
| 冷却 | `WAKE_COOLDOWN` | `3.0` | 应答后冷却秒数 |

命令行：`--config FILE --input-device X --output-device Y --model-dir D --responses-dir A --threshold T --hits N --channel C --cooldown S`。

## 调优（重要：换设备后必做）

模型分数对**距离/音量/口音**敏感，同一人正常喊话得分可能在 0.2~0.97 间波动。
直接经验值：先 `0.2 / hits=3`，然后跑校准工具按数据调整：

```bash
# 播放一声"滴"，然后录音 30 秒 —— 请在此期间正常音量喊 8 次 "Hey Jarvis"（间隔 2~3 秒）
.venv/bin/python tools/calibrate.py --record 30
```

工具会打印每个发声事件的得分和不同阈值下的命中数，据此选择：
漏唤醒多 → 降阈值；无人时误唤醒 → 升阈值或加大 `hits`（4~5 对真命中几乎无损）。

## 安装为系统服务（开机自启）

```bash
sudo scripts/install_service.sh /opt/hey-jarvis-wake voiceuser
# 可选第三个参数追加 Environment：sudo scripts/install_service.sh /opt/hey-jarvis-wake voiceuser "WAKE_INPUT_DEVICE=plughw:1,0"
journalctl -u hey-jarvis-wake -f    # 看日志
systemctl restart hey-jarvis-wake   # 改配置后重启
```

## 自测

```bash
# 正样本：tests/hey_jarvis.wav 应命中（peak≈1.0）
.venv/bin/python wake_service.py --test-wav tests/hey_jarvis.wav
# 负样本：tests/silence.wav 不应命中
.venv/bin/python wake_service.py --test-wav tests/silence.wav --expect-no-wake
# 单独试播一个应答文件
.venv/bin/python wake_service.py --play-response assets/response.wav
```

## 其他设备迁移备忘

- **ReSpeaker 等 USB 声卡**：即插即用，`arecord -l`/`aplay -l` 查设备号后配置 `plughw:N,0`（plug 自动做采样率/声道转换，最省心）
- **查看可用设备**：`arecord -L`（采集）/ `aplay -L`（播放）
- **平台支持**：目前实现 Linux + ALSA 后端（代码内预留了音频后端抽象接口，macOS/Windows 的 PortAudio 后端待做）
- **换唤醒词**：openwakeword 官方 release 有多个预训练词；自定义词需自训（openwakeword 训练流程），产物 `.onnx` 放入模型目录即可

## 许可

- **代码**：Apache-2.0（与 openWakeWord 一致），见 [LICENSE](LICENSE)
- **模型**：openWakeWord 官方预训练模型为 **CC BY-NC-SA 4.0**（非商业用途），见 [MODEL_LICENSE.md](MODEL_LICENSE.md)；模型文件不入库，由 `scripts/download_models.sh` 下载并校验 SHA256
  > ⚠️ 商业/展厅场景使用模型前请自行确认上游授权条款

## 实测参考（Reachy Mini，2026-09）

- 设备：Reachy Mini（USB 声卡，ALSA 共享入口 `reachymini_audio_src/sink`），树莓派 5，Debian 13
- 参数：threshold 0.20 + hits 3；校准录音显示真人得分 0.2~0.97，0.5 阈值时仅命中 ~1/3
- 结果：实声测试 4/4 命中，静置无环境误唤醒；单帧推理 ~25ms
