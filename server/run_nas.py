import os
import time
from pathlib import Path

for line in Path(__file__).with_name("app.env").read_text(encoding="utf-8").splitlines():
    if line.strip() and not line.lstrip().startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ[key] = value

os.environ["FRIDGE_DATA_DIR"] = "/app/data"
os.environ["PRINT_PI_LOCAL"] = ""
os.environ["ALLOW_DB_TRANSFER"] = ""
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("TZ", "Asia/Shanghai")
time.tzset()

import uvicorn

uvicorn.run("main:app", host="127.0.0.1", port=8001)
