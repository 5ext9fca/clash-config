"""Register a device in the host settings file and print its subscription token."""

import argparse
import fcntl
import os
from pathlib import Path
import secrets
import tempfile

import yaml


def register(path, name, device_type):
    # Lock the directory because atomic replacement changes the settings inode.
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        try:
            raw = path.read_bytes()
            owner = path.stat()
            settings = yaml.safe_load(raw)
        except FileNotFoundError:
            owner = None
            example = Path(__file__).with_name("settings.example.yaml")
            settings = yaml.safe_load(example.read_bytes())
            settings["devices"] = []
        if not isinstance(settings, dict):
            raise ValueError("settings 必须是 YAML 映射")
        devices = settings.setdefault("devices", [])
        if not isinstance(devices, list) or any(not isinstance(d, dict) for d in devices):
            raise ValueError("devices 必须是设备列表")
        if any(d.get("name") == name for d in devices):
            raise ValueError("设备名已存在")
        token = secrets.token_urlsafe(32)
        while any(d.get("token") == token for d in devices):
            token = secrets.token_urlsafe(32)
        devices.append({"name": name, "type": device_type, "token": token})
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            try:
                if owner:
                    current = os.fstat(tmp.fileno())
                    if (current.st_uid, current.st_gid) != (owner.st_uid, owner.st_gid):
                        os.fchown(tmp.fileno(), owner.st_uid, owner.st_gid)
                    os.fchmod(tmp.fileno(), owner.st_mode & 0o777)
                tmp.write(yaml.safe_dump(settings, sort_keys=False, allow_unicode=True).encode())
                tmp.flush()
                os.fsync(tmp.fileno())
                os.replace(tmp.name, path)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
        return token
    finally:
        os.close(directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("type", choices=("desktop", "mobile", "router"))
    parser.add_argument("--settings", type=Path, default=Path(__file__).with_name("settings.yaml"))
    args = parser.parse_args()
    if not args.name.strip():
        parser.error("设备名不能为空")
    try:
        print(register(args.settings.resolve(), args.name.strip(), args.type))
    except yaml.YAMLError:
        parser.exit(1, "settings YAML 格式无效\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
