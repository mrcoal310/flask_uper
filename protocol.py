import json
import time
import uuid
from typing import Any, Dict, Optional


def unix_ts() -> int:
    return int(time.time())


def new_req_id(prefix: str = "pc") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def build_envelope(
    *,
    product_id: str,
    device_id: str,
    proto_ver: str,
    msg_type: str,
    data: Dict[str, Any],
    req_id: Optional[str] = None,
    seq: int = 0,
    error: Optional[Dict[str, Any]] = None,
    ext: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "proto_ver": proto_ver,
        "product_id": product_id,
        "device_id": device_id,
        "msg_type": msg_type,
        "req_id": req_id or "",
        "seq": seq,
        "timestamp": unix_ts(),
        "data": data,
        "error": error,
        "ext": ext or {"source": "flask_upper_computer"},
    }


def dumps_compact(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def parse_json_payload(payload: bytes | str) -> Dict[str, Any]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    return json.loads(payload)


def make_switch_cmd(value: int) -> Dict[str, Any]:
    if value not in (0, 1):
        raise ValueError("switch must be 0 or 1")
    return {"cmd": "switch_set", "switch": int(value)}


def make_timer_set_cmd(action: str, delay_s: int) -> Dict[str, Any]:
    action = action.strip().lower()
    if action not in {"on", "off"}:
        raise ValueError("action must be 'on' or 'off'")
    delay_s = int(delay_s)
    if delay_s < 1 or delay_s > 86400:
        raise ValueError("delay_s must be in range 1..86400")
    return {"cmd": "timer_set", "action": action, "delay_s": delay_s}


def make_timer_cancel_cmd() -> Dict[str, Any]:
    return {"cmd": "timer_cancel"}


def make_timer_query_cmd() -> Dict[str, Any]:
    return {"cmd": "timer_query"}


def make_status_query_cmd() -> Dict[str, Any]:
    return {"cmd": "status_query"}


def make_restart_cmd() -> Dict[str, Any]:
    return {"cmd": "restart"}


def make_config_set_cmd(data: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "report_period_s",
        "temp_high_limit",
        "humidity_high_limit",
        "auto_rule_enable",
        "web_token",
    }
    cmd = {"cmd": "config_set"}
    for key, value in data.items():
        if key in allowed:
            cmd[key] = value

    if "report_period_s" in cmd:
        period = int(cmd["report_period_s"])
        if period < 2 or period > 3600:
            raise ValueError("report_period_s must be in range 2..3600")
        cmd["report_period_s"] = period

    if "temp_high_limit" in cmd:
        limit = float(cmd["temp_high_limit"])
        if limit < -20.0 or limit > 80.0:
            raise ValueError("temp_high_limit must be in range -20..80")
        cmd["temp_high_limit"] = limit

    if "humidity_high_limit" in cmd:
        limit = float(cmd["humidity_high_limit"])
        if limit < 0.0 or limit > 100.0:
            raise ValueError("humidity_high_limit must be in range 0..100")
        cmd["humidity_high_limit"] = limit

    if "auto_rule_enable" in cmd:
        cmd["auto_rule_enable"] = bool(cmd["auto_rule_enable"])

    if len(cmd) == 1:
        raise ValueError("no valid config field supplied")
    return cmd
