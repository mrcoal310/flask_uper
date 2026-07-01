import json
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import paho.mqtt.client as mqtt

from config import AppConfig
from protocol import build_envelope, dumps_compact, new_req_id, parse_json_payload
from storage import Storage


class MqttWorker:
    _DATA_MESSAGE_TYPES = {"telemetry", "status", "heartbeat"}
    _VOLATILE_STATE_KEYS = (
        "temperature",
        "humidity",
        "sensor_ok",
        "switch",
        "timer_enable",
        "timer_action",
        "timer_remain_s",
        "report_period_s",
        "rssi",
        "fw_ver",
    )
    _POWER_OFF_CLEAR_KEYS = (
        "temperature",
        "humidity",
        "sensor_ok",
    )
    _TRANSIENT_STATE_KEYS = (
        "last_seen_ts",
        "last_data_ts",
        "last_telemetry",
        "last_status",
        "last_reply",
        "last_event",
        "last_heartbeat",
    )

    def __init__(self, cfg: AppConfig, storage: Storage) -> None:
        self.cfg = cfg
        self.storage = storage
        self._lock = threading.RLock()
        self._started = False
        self._seq = 0
        self._stop_event = threading.Event()
        self._client: Optional[mqtt.Client] = None
        self._client_id = cfg.mqtt_client_id or f"upper-{cfg.device_id}-{uuid.uuid4().hex[:8]}"
        self._last_telemetry_signature_by_device: Dict[str, str] = {}
        self._broker_state: Dict[str, Any] = {
            "mqtt_connected": False,
            "mqtt_error": "not started",
        }
        self._device_states: Dict[str, Dict[str, Any]] = {
            cfg.device_id: self._new_device_state(),
        }
        self._recent_messages: Deque[Dict[str, Any]] = deque(maxlen=cfg.recent_message_limit)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop_event.clear()

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id, protocol=mqtt.MQTTv311)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=10)

        username = self.cfg.mqtt_auth_username
        password = self.cfg.mqtt_auth_password or None
        if username:
            client.username_pw_set(username, password)
        if self.cfg.mqtt_tls:
            client.tls_set()

        with self._lock:
            self._client = client
            self._broker_state["mqtt_error"] = "connecting"

        client.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, self.cfg.mqtt_keepalive)
        client.loop_start()

    def stop(self) -> None:
        with self._lock:
            self._started = False
            self._stop_event.set()
            client = self._client
            self._client = None
        if client is None:
            return
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del userdata, flags, properties
        if _reason_code_failed(reason_code):
            self._mark_error(f"mqtt connect failed: {reason_code}")
            return

        subscriptions = [(topic, 1) for topic in self.cfg.receive_topics() if topic]
        if subscriptions:
            client.subscribe(subscriptions)

        with self._lock:
            self._broker_state["mqtt_connected"] = True
            self._broker_state["mqtt_error"] = ""

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del client, userdata, disconnect_flags, properties
        if self._stop_event.is_set():
            message = "stopped"
        elif not _reason_code_failed(reason_code):
            message = "disconnected"
        else:
            message = f"mqtt disconnected: {reason_code}"
        self._mark_error(message)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        del client, userdata
        now = int(time.time())
        raw_text = _decode_payload(msg.payload)
        payload = self._normalize_payload(msg.topic, raw_text, now)
        self._ingest_message(msg.topic, payload, now)

    def _normalize_payload(self, topic: str, raw_text: str, now: int) -> Dict[str, Any]:
        inferred = _infer_msg_type_from_topic(topic)
        try:
            parsed = parse_json_payload(raw_text)
        except Exception:
            return {
                "msg_type": inferred or "raw",
                "timestamp": now,
                "device_id": self.cfg.device_id,
                "req_id": "",
                "data": {"raw": raw_text},
                "error": None,
                "ext": {},
            }

        if not isinstance(parsed, dict):
            return {
                "msg_type": inferred or "raw",
                "timestamp": now,
                "device_id": self.cfg.device_id,
                "req_id": "",
                "data": {"value": parsed},
                "error": None,
                "ext": {},
            }

        if "msg_type" in parsed and isinstance(parsed.get("data"), dict):
            return parsed

        reserved = {"proto_ver", "product_id", "device_id", "msg_type", "req_id", "seq", "timestamp", "data", "error", "ext"}
        data = parsed.get("data") if isinstance(parsed.get("data"), dict) else {k: v for k, v in parsed.items() if k not in reserved}
        if not data:
            data = dict(parsed)

        return {
            "proto_ver": str(parsed.get("proto_ver") or self.cfg.proto_ver),
            "product_id": str(parsed.get("product_id") or self.cfg.product_id),
            "device_id": str(parsed.get("device_id") or self.cfg.device_id),
            "msg_type": str(parsed.get("msg_type") or inferred or "raw"),
            "req_id": str(parsed.get("req_id") or ""),
            "seq": parsed.get("seq", 0),
            "timestamp": parsed.get("timestamp", now),
            "data": data,
            "error": parsed.get("error"),
            "ext": parsed.get("ext") if isinstance(parsed.get("ext"), dict) else {},
        }

    def _ingest_message(self, topic: str, payload: Dict[str, Any], now: int) -> None:
        msg_type = str(payload.get("msg_type") or "raw")
        device_id = _payload_device_id(payload, self.cfg.device_id)
        item = {
            "ts": now,
            "topic": topic,
            "msg_type": msg_type,
            "device_id": device_id,
            "payload": payload,
        }

        with self._lock:
            self._recent_messages.appendleft(item)
            state = self._ensure_device_state_locked(device_id)
            state["last_seen_ts"] = now

            if msg_type in self._DATA_MESSAGE_TYPES:
                state["last_data_ts"] = now

            if msg_type == "telemetry":
                state["last_telemetry"] = payload
                state["online"] = True
                self._merge_data_fields(state, payload)
            elif msg_type == "status":
                state["last_status"] = payload
                state["online"] = True
                self._merge_data_fields(state, payload)
            elif msg_type == "reply":
                state["last_reply"] = payload
            elif msg_type == "event":
                state["last_event"] = payload
            elif msg_type == "heartbeat":
                state["last_heartbeat"] = payload
                state["online"] = True
                self._merge_data_fields(state, payload)
            elif msg_type == "lwt":
                state["last_event"] = payload
                self._merge_data_fields(state, payload)

        if msg_type == "telemetry":
            signature = dumps_compact(payload)
            last_signature = self._last_telemetry_signature_by_device.get(device_id)
            if signature != last_signature:
                self.storage.insert_telemetry(payload)
                self._last_telemetry_signature_by_device[device_id] = signature
        elif msg_type in {"event", "reply", "status", "lwt"}:
            self.storage.insert_event(payload, msg_type)

    def _merge_data_fields(self, state: Dict[str, Any], payload: Dict[str, Any]) -> None:
        data = payload.get("data") or {}
        mapping = {
            "temperature": "temperature",
            "humidity": "humidity",
            "sensor_ok": "sensor_ok",
            "switch": "switch",
            "timer_enable": "timer_enable",
            "timer_action": "timer_action",
            "timer_remain_s": "timer_remain_s",
            "report_period_s": "report_period_s",
            "rssi": "rssi",
        }
        for src, dst in mapping.items():
            if src in data:
                state[dst] = data[src]
        ext = payload.get("ext") or {}
        fw_ver = ext.get("fw_ver", data.get("fw_ver"))
        if fw_ver not in (None, ""):
            state["fw_ver"] = fw_ver
        if "online" in data:
            state["online"] = bool(data["online"])

    def _mark_error(self, message: str) -> None:
        with self._lock:
            self._broker_state["mqtt_connected"] = False
            self._broker_state["mqtt_error"] = message

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def publish_command(self, cmd_data: Dict[str, Any], req_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any]:
        target_device_id = self._resolve_device_id(device_id)
        req_id = req_id or new_req_id("pc")
        payload = build_envelope(
            product_id=self.cfg.product_id,
            device_id=target_device_id,
            proto_ver=self.cfg.proto_ver,
            msg_type="cmd",
            req_id=req_id,
            seq=self._next_seq(),
            data=cmd_data,
        )

        with self._lock:
            client = self._client
        if client is None or not client.is_connected():
            raise RuntimeError("MQTT broker is not connected yet.")

        topic = self.cfg.send_topic(target_device_id)
        text = dumps_compact(payload)
        info = client.publish(topic, text, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"mqtt publish failed: rc={info.rc}")

        with self._lock:
            self._ensure_device_state_locked(target_device_id)
            self._recent_messages.appendleft(
                {
                    "ts": int(time.time()),
                    "topic": topic,
                    "msg_type": "cmd_out",
                    "device_id": target_device_id,
                    "payload": payload,
                }
            )
        return payload

    def snapshot(self, device_id: Optional[str] = None) -> Dict[str, Any]:
        selected_device_id = self._resolve_device_id(device_id)
        with self._lock:
            self._refresh_broker_state_locked()
            state = self._effective_state_locked(selected_device_id)
            return {
                "selected_device_id": selected_device_id,
                "state": state,
                "devices": self._device_summaries_locked(selected_device_id),
                "recent_messages": self._recent_messages_for_device_locked(selected_device_id),
                "topics": self.topics(selected_device_id),
                "mqtt": {
                    "host": self.cfg.mqtt_host,
                    "port": self.cfg.mqtt_port,
                    "client_id": self._client_id,
                    "base_topic": self.cfg.base_topic_for(selected_device_id),
                    "mode": "mqtt-broker",
                },
            }

    def topics(self, device_id: Optional[str] = None) -> Dict[str, Any]:
        selected_device_id = self._resolve_device_id(device_id)
        return {
            "receive": self.cfg.receive_topics(selected_device_id),
            "send": self.cfg.send_topic(selected_device_id),
            "base_topic": self.cfg.base_topic_for(selected_device_id),
        }

    def known_devices(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._refresh_broker_state_locked()
            return self._device_summaries_locked(self.cfg.device_id)

    def _refresh_broker_state_locked(self) -> None:
        client = self._client
        if client is None:
            self._broker_state["mqtt_connected"] = False
            if not self._stop_event.is_set() and self._started and self._broker_state["mqtt_error"] == "":
                self._broker_state["mqtt_error"] = "mqtt client unavailable"
            return

        if not client.is_connected():
            self._broker_state["mqtt_connected"] = False
            if not self._stop_event.is_set() and self._broker_state["mqtt_error"] == "":
                self._broker_state["mqtt_error"] = "disconnected"
            return

        last_msg_in = _safe_float(getattr(client, "_last_msg_in", None))
        if last_msg_in is not None:
            silent_seconds = time.monotonic() - last_msg_in
            if silent_seconds > self._broker_timeout_seconds():
                self._broker_state["mqtt_connected"] = False
                self._broker_state["mqtt_error"] = f"mqtt keepalive timeout ({int(silent_seconds)}s)"
                return

        self._broker_state["mqtt_connected"] = True
        self._broker_state["mqtt_error"] = ""

    def _device_summaries_locked(self, selected_device_id: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for device_id in sorted(self._device_states):
            effective = self._effective_state_locked(device_id)
            rows.append(
                {
                    "device_id": device_id,
                    "selected": device_id == selected_device_id,
                    "online": bool(effective.get("online")),
                    "last_seen_ts": effective.get("last_seen_ts"),
                    "switch": effective.get("switch"),
                }
            )
        rows.sort(key=lambda item: (0 if item["selected"] else 1, 0 if item["online"] else 1, -(item["last_seen_ts"] or 0), item["device_id"]))
        return rows

    def _recent_messages_for_device_locked(self, device_id: str) -> List[Dict[str, Any]]:
        return [item for item in self._recent_messages if item.get("device_id") == device_id]

    def _effective_state_locked(self, device_id: str) -> Dict[str, Any]:
        raw_state = self._ensure_device_state_locked(device_id)
        state = dict(raw_state)
        state.update(self._broker_state)
        state["device_id"] = device_id

        if not state.get("mqtt_connected"):
            return self._startup_like_state(state)

        if self._is_device_stale(state):
            state["online"] = False

        if not state.get("online"):
            for key in self._VOLATILE_STATE_KEYS:
                state[key] = None
        else:
            if _safe_int(state.get("timer_enable")) == 0:
                state["timer_action"] = None
                state["timer_remain_s"] = None
            if _safe_int(state.get("switch")) == 0:
                for key in self._POWER_OFF_CLEAR_KEYS:
                    state[key] = None
        return state

    def _startup_like_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state["online"] = False
        for key in self._VOLATILE_STATE_KEYS:
            state[key] = None
        for key in self._TRANSIENT_STATE_KEYS:
            state[key] = None
        return state

    def _is_device_stale(self, state: Dict[str, Any]) -> bool:
        last_data_ts = state.get("last_data_ts")
        if last_data_ts is None:
            return False

        try:
            last_data_ts = int(last_data_ts)
        except Exception:
            return False

        return int(time.time()) - last_data_ts > self._offline_timeout_seconds(state)

    def _offline_timeout_seconds(self, state: Dict[str, Any]) -> int:
        report_period = _safe_int(state.get("report_period_s"))
        if report_period is None or report_period < 1:
            report_period = 5

        return report_period + 2

    def _broker_timeout_seconds(self) -> int:
        keepalive = max(5, int(self.cfg.mqtt_keepalive))
        return max(keepalive * 2, keepalive + 10)

    def _resolve_device_id(self, device_id: Optional[str]) -> str:
        text = str(device_id or "").strip()
        return text or self.cfg.device_id

    def _ensure_device_state_locked(self, device_id: str) -> Dict[str, Any]:
        resolved = self._resolve_device_id(device_id)
        state = self._device_states.get(resolved)
        if state is None:
            state = self._new_device_state()
            self._device_states[resolved] = state
        return state

    @staticmethod
    def _new_device_state() -> Dict[str, Any]:
        return {
            "last_seen_ts": None,
            "last_data_ts": None,
            "last_telemetry": None,
            "last_status": None,
            "last_reply": None,
            "last_event": None,
            "last_heartbeat": None,
            "online": False,
            "temperature": None,
            "humidity": None,
            "sensor_ok": None,
            "switch": None,
            "timer_enable": None,
            "timer_action": None,
            "timer_remain_s": None,
            "report_period_s": None,
            "rssi": None,
            "fw_ver": None,
        }


def _decode_payload(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("gb2312", errors="replace")


def _infer_msg_type_from_topic(topic: str) -> Optional[str]:
    suffix = topic.rsplit("/", 1)[-1].strip().lower()
    if suffix == "data":
        return "telemetry"
    if suffix in {"telemetry", "status", "reply", "event", "heartbeat", "lwt"}:
        return suffix
    return None


def _payload_device_id(payload: Dict[str, Any], default_device_id: str) -> str:
    raw = payload.get("device_id")
    text = str(raw or "").strip()
    return text or default_device_id


def _reason_code_failed(reason_code: Any) -> bool:
    is_failure = getattr(reason_code, "is_failure", None)
    if is_failure is not None:
        try:
            return bool(is_failure)
        except Exception:
            pass

    value = getattr(reason_code, "value", reason_code)
    try:
        return int(value) != 0
    except Exception:
        return str(reason_code).lower() not in {"0", "success"}


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None
