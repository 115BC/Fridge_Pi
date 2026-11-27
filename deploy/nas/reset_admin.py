import os
import secrets
import string
import sys
from pathlib import Path


def main() -> None:
    env_path = Path("/app/server/app.env")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key] = value

    os.environ["FRIDGE_DATA_DIR"] = "/app/data"
    os.environ["PRINT_PI_LOCAL"] = ""
    os.environ["ALLOW_DB_TRANSFER"] = ""
    sys.path.insert(0, "/app/server")

    import auth
    import db

    c = db.conn()
    if "--check" in sys.argv:
        location = "mysql" if db.USING_MYSQL else str(db.DB_PATH)
        count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        print("数据库：" + location)
        print("用户数：" + str(count))
        return

    row = c.execute(
        "SELECT id, web_username FROM users WHERE role='admin' ORDER BY id LIMIT 1"
    ).fetchone()
    if not row:
        raise SystemExit("数据库中没有超管账号")

    groups = [
        string.ascii_uppercase,
        string.ascii_lowercase,
        string.digits,
        "!@#$%+-_",
    ]
    alphabet = string.ascii_letters + string.digits + "!@#$%+-_"
    chars = [secrets.choice(group) for group in groups]
    chars += [secrets.choice(alphabet) for _ in range(10)]
    secrets.SystemRandom().shuffle(chars)
    password = "".join(chars)

    error = auth.validate_password(password)
    if error:
        raise SystemExit(error)

    c.execute(
        "UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
        (auth.hash_password(password), row["id"]),
    )

    lines = env_path.read_text(encoding="utf-8").splitlines()
    lines = [
        line for line in lines
        if not line.startswith("WEB_BOOTSTRAP_USERNAME=")
        and not line.startswith("WEB_BOOTSTRAP_PASSWORD=")
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    location = "mysql" if db.USING_MYSQL else str(db.DB_PATH)
    print("数据库：" + location)
    print("超管账号：" + (row["web_username"] or "admin"))
    print("新临时密码：" + password)


if __name__ == "__main__":
    main()
