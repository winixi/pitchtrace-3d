"""读取 Live 签发的设备凭据，并只在安全的连接上附加到写入请求。

凭据来源依次为环境变量 PITCHTRACE_DEVICE_TOKEN 和比赛配置的 deviceTokenFile（相对配置文件目录，
权限须为仅本人可读写）。凭据不写入运行记录、日志或错误信息。
"""

import ipaddress
import os
import re
import stat
from urllib.parse import urlsplit

TOKEN = re.compile(r"pt1\.[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]{43}")
ENV_TOKEN = "PITCHTRACE_DEVICE_TOKEN"
ENV_ALLOW_HTTP = "PITCHTRACE_GATEWAY_ALLOW_HTTP"
# 只影响上行交付，不参与运行配置指纹。
DELIVERY_KEYS = ("deviceTokenFile", "gatewayAllowHttp")


def _checked(token, source):
    token = token.strip()
    if not TOKEN.fullmatch(token):
        raise ValueError(f"{source} 中的设备凭据格式无效，应为 Live 签发的 pt1.<设备 ID>.<密钥>")
    return token


def load_device_token(settings, folder):
    value = os.environ.get(ENV_TOKEN)
    if value:
        return _checked(value, ENV_TOKEN)
    name = settings.get("deviceTokenFile")
    if name is None:
        return None
    if not isinstance(name, str) or not name:
        raise ValueError("deviceTokenFile 必须是凭据文件路径")
    path = folder / name
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise ValueError(f"设备凭据文件 {path} 须为仅本人可读写的普通文件，请执行 chmod 600")
    return _checked(path.read_text(encoding="utf-8"), str(path))


def allows_http(settings):
    value = settings.get("gatewayAllowHttp", False)
    if type(value) is not bool:
        raise ValueError("gatewayAllowHttp 必须是布尔值")
    return value or os.environ.get(ENV_ALLOW_HTTP) == "1"


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def auth_headers(gateway, token, allow_http=False):
    """凭据只经 HTTPS 或回环地址发送；隔离的现场局域网需显式允许明文 HTTP。"""
    if not token:
        return {}
    url = urlsplit(gateway)
    if url.scheme != "https" and not _loopback(url.hostname or "") and not allow_http:
        raise ValueError("设备凭据只经 HTTPS 或回环地址发送；隔离的现场局域网可设 gatewayAllowHttp")
    return {"Authorization": f"Bearer {token}"}


def child_environment(token, allow_http):
    """传给 publish-scenes、publish-media 子进程的环境。"""
    env = dict(os.environ)
    if token:
        env[ENV_TOKEN] = token
    if allow_http:
        env[ENV_ALLOW_HTTP] = "1"
    return env
