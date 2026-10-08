"""本地物品分类：ONNX Runtime + MobileNetV3（ImageNet 预训练）。

模型只给出"建议分类"，最终由学生在触控屏上确认/修正。
未下载模型时自动降级为 other，不影响其他功能。
"""
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent / "models"

CATEGORIES = ["水果", "蔬菜", "奶制品", "肉禽", "水产", "饮料", "零食熟食", "餐盒剩菜", "其他"]

# ImageNet 英文标签子串 -> 本地分类（建议用途，人工修正兜底）
KEYWORD_MAP = {
    "水果": ["apple", "banana", "orange", "lemon", "pear", "grape", "strawberry",
           "watermelon", "melon", "peach", "pineapple", "mango", "cherry", "plum",
           "kiwi", "fig", "date", "pomegranate", "jackfruit", "custard apple"],
    "蔬菜": ["broccoli", "cauliflower", "cabbage", "lettuce", "spinach", "cucumber",
           "bell pepper", "tomato", "carrot", "radish", "potato", "onion", "garlic",
           "mushroom", "corn", "pea", "bean", "squash", "zucchini", "eggplant",
           "asparagus", "artichoke", "head cabbage"],
    "奶制品": ["cheese", "milk", "yogurt", "butter", "eggnog"],
    "肉禽": ["beef", "pork", "lamb", "ham", "sausage", "bacon", "salami", "meat loaf",
           "rib", "liver", "tongue", "chicken", "turkey", "goose", "duck", "hen", "ostrich"],
    "水产": ["fish", "crab", "lobster", "shrimp", "mussel", "oyster", "squid",
           "octopus", "snail", "clam", "sea cucumber", "stingray", "shark", "crayfish"],
    "饮料": ["bottle", "pop bottle", "beer glass", "coffee mug", "cup", "wine bottle",
           "water jug", "pitcher"],
    "零食熟食": ["bagel", "pretzel", "french loaf", "pizza", "burrito", "hotdog",
             "hamburger", "cheeseburger", "chocolate", "cookie", "cracker"],
    "餐盒剩菜": ["plate", "tray", "pot", "wok", "soup bowl", "mixing bowl",
             "container", "carton", "ice cream", "custard", "pudding", "trifle",
             "meatball", "guacamole", "consomme"],
}


def _load_labels(path: Path) -> list[str]:
    labels = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        labels.append(parts[1].strip().lower() if len(parts) > 1 else line.strip().lower())
    return labels


def _map_category(label: str) -> str:
    for cat, words in KEYWORD_MAP.items():
        if any(w in label for w in words):
            return cat
    return "其他"


class LocalClassifier:
    def __init__(self, model_path: Path = MODELS_DIR / "mobilenetv3.onnx",
                 labels_path: Path = MODELS_DIR / "labels.txt"):
        self.sess = None
        self.labels = []
        if model_path.exists() and labels_path.exists():
            try:
                import onnxruntime as ort
                self.sess = ort.InferenceSession(str(model_path),
                                                 providers=["CPUExecutionProvider"])
                self.labels = _load_labels(labels_path)
            except Exception as e:
                print(f"[classify] 模型加载失败，降级为无分类: {e}")
        else:
            print("[classify] 未找到模型文件，分类功能停用（运行 scripts/download_model.py）")

    def classify(self, image_path: Path) -> dict:
        if self.sess is None:
            return {"category": "其他", "confidence": 0.0, "top": []}
        import cv2
        img = cv2.imread(str(image_path))
        if img is None:
            return {"category": "其他", "confidence": 0.0, "top": []}
        img = cv2.resize(img, (224, 224))[:, :, ::-1].astype(np.float32) / 255.0
        img = (img - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        blob = img.transpose(2, 0, 1)[None].astype(np.float32)
        logits = self.sess.run(None, {self.sess.get_inputs()[0].name: blob})[0][0]
        probs = np.exp(logits - logits.max())
        probs /= probs.sum()
        top5 = np.argsort(probs)[::-1][:5]
        top = [{"label": self.labels[i], "p": round(float(probs[i]), 3),
                "category": _map_category(self.labels[i])} for i in top5]
        best = top[0]
        return {"category": best["category"], "confidence": best["p"], "top": top}
