import os
import secrets
import string
from pathlib import Path


def main() -> None:
    path = Path("/app/server/app.env")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value

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

    values["WEB_BOOTSTRAP_USERNAME"] = "admin"
    values["WEB_BOOTSTRAP_PASSWORD"] = password
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    print("首个超管：admin")
    print("临时密码：" + password)


if __name__ == "__main__":
    main()
