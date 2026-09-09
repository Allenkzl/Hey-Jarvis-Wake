# 模型来源与许可

本仓库**不包含**任何模型二进制文件。运行前执行 `scripts/download_models.sh`，
从 openWakeWord 官方 v0.5.1 release 下载以下 ONNX 模型：

- `hey_jarvis_v0.1.onnx` — 唤醒词分类器（"Hey Jarvis"）
- `embedding_model.onnx` — 音频嵌入模型
- `melspectrogram.onnx` — 频谱特征模型

来源：https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1

## 许可

openWakeWord 项目说明：代码为 Apache-2.0，包含的**预训练模型为
CC BY-NC-SA 4.0**（署名-非商业性使用-相同方式共享）。

- 模型许可全文：https://creativecommons.org/licenses/by-nc-sa/4.0/
- 上游说明：https://github.com/dscripka/openWakeWord#license

> ⚠️ **商业/展厅等非个人用途请先自行确认模型授权**；如需商业授权，
> 可考虑自训自定义模型（训练产物归训练者所有，但训练流程请遵守上游条款）。

## 完整性校验

下载脚本使用 `models/SHA256SUMS` 对三个文件逐一校验，确保与官方发布一致。
