"""生成一次性的解锁令牌，用于联调/演示：
    python token.py <secret> <物品编码>
输出可直接 curl 的完整命令。
"""
import hashlib
import hmac
import sys
import time

secret, code = sys.argv[1], sys.argv[2]
ts = int(time.time())
token = hmac.new(secret.encode(), f"{code}|{ts}".encode(), hashlib.sha256).hexdigest()
print(f"curl -X POST http://<树莓派IP>:8000/api/unlock -H 'Content-Type: application/json' "
      f"-d '{{\"code\": \"{code}\", \"ts\": {ts}, \"token\": \"{token}\"}}'")
