import os
from dataclasses import dataclass
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # python-dotenv is optional at import time. It is listed in requirements.txt.
    pass


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _str_env(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip()


def _mqtt_endpoint_parts() -> tuple[str, int]:
    raw = _str_env("MQTT_HOST") or _str_env("MQTT_HOST_URL")
    default_host = "127.0.0.1"
    default_port = _int_env("MQTT_PORT", 1883)

    if not raw:
        return default_host, default_port

    candidate = raw if "://" in raw else f"mqtt://{raw}"
    parsed = urlparse(candidate)
    host = parsed.hostname or raw.split("/", 1)[0].split(":", 1)[0].strip()
    port = parsed.port or default_port
    return host or default_host, port


_MQTT_HOST, _MQTT_PORT = _mqtt_endpoint_parts()


@dataclass(frozen=True)
class AppConfig:
    product_id: str = os.getenv("APP_PRODUCT_ID", "envctrl_v1")
    device_id: str = os.getenv("APP_DEVICE_ID", "iot_node_001")
    proto_ver: str = os.getenv("APP_PROTO_VER", "1.0")

    mqtt_host: str = _MQTT_HOST
    mqtt_port: int = _MQTT_PORT
    mqtt_client_id: str = _str_env("MQTT_CLIENT_ID")
    mqtt_username: str = os.getenv("MQTT_USERNAME", "")
    mqtt_password: str = os.getenv("MQTT_PASSWORD", "")
    mqtt_keepalive: int = _int_env("MQTT_KEEPALIVE", 30)
    mqtt_tls: bool = _bool_env("MQTT_TLS", False)

    # If empty, defaults to iot/{product_id}/{device_id}
    mqtt_base_topic: str = os.getenv("MQTT_BASE_TOPIC", "")
    mqtt_receive_topic: str = _str_env("MQTT_RECEIVE_TOPIC", "/k0xzrztwuSU/Android/user/DATA")
    mqtt_send_topic: str = _str_env("MQTT_SEND_TOPIC", "/k0xzrztwuSU/Android/user/SEETING")

    # Optional token-only MQTT authentication.
    api_token: str = _str_env("API_TOKEN")
    sqlite_path: str = os.getenv("SQLITE_PATH", "instance/iot_upper_computer.db")

    flask_host: str = os.getenv("FLASK_HOST", "0.0.0.0")
    flask_port: int = _int_env("FLASK_PORT", 5000)
    flask_debug: bool = _bool_env("FLASK_DEBUG", False)

    history_limit_default: int = _int_env("HISTORY_LIMIT_DEFAULT", 200)
    recent_message_limit: int = _int_env("RECENT_MESSAGE_LIMIT", 100)

    @property
    def mqtt_auth_enabled(self) -> bool:
        return bool(self.mqtt_username or self.mqtt_password or self.api_token)

    @property
    def mqtt_auth_username(self) -> str:
        if self.mqtt_username:
            return self.mqtt_username
        if self.api_token:
            # Token-only brokers commonly use the token as the MQTT username.
            return self.api_token
        return ""

    @property
    def mqtt_auth_password(self) -> str:
        if self.mqtt_password:
            return self.mqtt_password
        if self.mqtt_username and self.api_token:
            return self.api_token
        return ""

    @property
    def base_topic(self) -> str:
        return self.base_topic_for()

    def base_topic_for(self, device_id: str | None = None) -> str:
        if self.mqtt_base_topic:
            return self.mqtt_base_topic.strip("/")
        return f"iot/{self.product_id}/{device_id or self.device_id}"

    def topic(self, name: str, device_id: str | None = None) -> str:
        return f"{self.base_topic_for(device_id)}/{name}"

    def receive_topic(self, device_id: str | None = None) -> str:
        topics = self.receive_topics(device_id)
        return topics[0] if topics else ""

    def receive_topics(self, device_id: str | None = None) -> list[str]:
        if self.mqtt_receive_topic:
            return [self.mqtt_receive_topic]
        if self.mqtt_base_topic:
            base = self.base_topic_for(device_id)
        else:
            target_device_id = device_id or "+"
            base = f"iot/{self.product_id}/{target_device_id}"
        return [
            f"{base}/telemetry",
            f"{base}/status",
            f"{base}/reply",
            f"{base}/event",
            f"{base}/heartbeat",
            f"{base}/lwt",
        ]

    def send_topic(self, device_id: str | None = None) -> str:
        if self.mqtt_send_topic:
            return self.mqtt_send_topic
        return self.topic("cmd", device_id)
