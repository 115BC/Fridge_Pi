"""下载 MobileNetV3 ONNX 模型与 ImageNet 类别表到 pi/models/。

在有外网的电脑上运行：python scripts/download_model.py
（树莓派无外网时，也可手动下载后拷贝到 models/ 目录）
"""
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
FILES = {
    "mobilenetv3.onnx": ("https://github.com/onnx/models/raw/main/Computer_Vision/"
                         "mobilenetv3_large_100_Opset17_timm/"
                         "mobilenetv3_large_100_Opset17.onnx"),
    "labels.txt": "https://raw.githubusercontent.com/pytorch/hub/master/imagenet_classes.txt",
}

MODELS_DIR.mkdir(exist_ok=True)
for name, url in FILES.items():
    dst = MODELS_DIR / name
    if dst.exists() and dst.stat().st_size > 1024:
        print(f"已存在: {dst}")
        continue
    print(f"下载 {name} ...")
    urllib.request.urlretrieve(url, dst)
    print(f"完成: {dst} ({dst.stat().st_size // 1024} KB)")
print("全部就绪。")
