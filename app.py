from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from flask import Flask, Response, jsonify, render_template, request

from config import AppConfig
from mqtt_worker import MqttWorker
from protocol import (
    make_config_set_cmd,
    make_restart_cmd,
    make_status_query_cmd,
    make_switch_cmd,
    make_timer_cancel_cmd,
    make_timer_query_cmd,
    make_timer_set_cmd,
)
from storage import Storage

cfg = AppConfig()
storage = Storage(cfg.sqlite_path)
mqtt_worker = MqttWorker(cfg, storage)

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


def _error(message: str, code: int = 400) -> tuple[Response, int]:
    return jsonify({"code": code, "message": message, "data": None}), code


def _request_device_id(body: Dict[str, Any] | None = None) -> str:
    candidate = request.args.get("device_id")
    if candidate is None and isinstance(body, dict):
        candidate = body.get("device_id")
    text = str(candidate or "").strip()
    return text or cfg.device_id


def _managed_device_id(body: Dict[str, Any] | None = None) -> str:
    candidate = None
    if isinstance(body, dict):
        candidate = body.get("device_id")
    text = str(candidate or "").strip()
    if not text:
        raise ValueError("device_id is required")
    if len(text) > 128:
        raise ValueError("device_id is too long")
    return text


def _parse_optional_datetime_arg(name: str) -> int | None:
    raw = str(request.args.get(name, "") or "").strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid datetime") from exc

    return int(dt.timestamp())


def _history_query_args(default_limit: int) -> tuple[int, str, int | None, int | None]:
    limit = int(request.args.get("limit", default_limit))
    device_id = _request_device_id()
    start_ts = _parse_optional_datetime_arg("start_at")
    end_ts = _parse_optional_datetime_arg("end_at")

    if start_ts is not None and end_ts is not None and start_ts > end_ts:
        raise ValueError("start_at must be earlier than or equal to end_at")

    return limit, device_id, start_ts, end_ts


def _device_catalog() -> list[Dict[str, Any]]:
    runtime_map = {item["device_id"]: item for item in mqtt_worker.known_devices()}
    deleted_ids = set(storage.deleted_device_ids())
    ordered_ids: list[str] = []
    seen: set[str] = set()

    for device_id in [cfg.device_id, *storage.managed_device_ids(), *storage.known_device_ids(), *runtime_map.keys()]:
        if device_id in deleted_ids and device_id != cfg.device_id:
            continue
        if device_id and device_id not in seen:
            seen.add(device_id)
            ordered_ids.append(device_id)

    rows: list[Dict[str, Any]] = []
    for device_id in ordered_ids:
        runtime = runtime_map.get(device_id, {})
        rows.append(
            {
                "device_id": device_id,
                "online": bool(runtime.get("online", False)),
                "last_seen_ts": runtime.get("last_seen_ts"),
                "switch": runtime.get("switch"),
                "selected": False,
                "removable": device_id != cfg.device_id,
            }
        )
    rows.sort(key=lambda item: (0 if item["device_id"] == cfg.device_id else 1, 0 if item["online"] else 1, -(item["last_seen_ts"] or 0), item["device_id"]))
    return rows


def _resolve_selected_device_id(candidate: str | None = None) -> str:
    text = str(candidate or "").strip()
    catalog = _device_catalog()
    device_ids = [item["device_id"] for item in catalog]

    if text and text in device_ids:
        return text
    if cfg.device_id in device_ids:
        return cfg.device_id
    if device_ids:
        return device_ids[0]
    return cfg.device_id


def _device_rows(selected_device_id: str) -> list[Dict[str, Any]]:
    rows = []
    for item in _device_catalog():
        row = dict(item)
        row["selected"] = row["device_id"] == selected_device_id
        rows.append(row)
    return rows


@app.route("/")
def index() -> str:
    return render_template(
        "index.html",
        product_id=cfg.product_id,
        device_id=cfg.device_id,
        default_device_id=cfg.device_id,
        mqtt_host=cfg.mqtt_host,
        mqtt_port=cfg.mqtt_port,
        mqtt_auth_enabled=cfg.mqtt_auth_enabled,
    )


@app.route("/api/status")
def api_status() -> Response:
    selected_device_id = _resolve_selected_device_id(_request_device_id())
    data = mqtt_worker.snapshot(selected_device_id)
    data["selected_device_id"] = selected_device_id
    data["devices"] = _device_rows(selected_device_id)
    return jsonify({"code": 0, "message": "ok", "data": data})


@app.route("/api/topics")
def api_topics() -> Response:
    return jsonify({"code": 0, "message": "ok", "data": mqtt_worker.topics(_request_device_id())})


@app.route("/api/devices", methods=["GET", "POST"])
def api_devices() -> tuple[Response, int] | Response:
    if request.method == "GET":
        selected_device_id = _resolve_selected_device_id(_request_device_id())
        return jsonify(
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "selected_device_id": selected_device_id,
                    "devices": _device_rows(selected_device_id),
                },
            }
        )

    body = request.get_json(silent=True) or {}
    try:
        device_id = storage.add_managed_device(_managed_device_id(body))
        selected_device_id = _resolve_selected_device_id(device_id)
        return jsonify(
            {
                "code": 0,
                "message": "created",
                "data": {
                    "device_id": device_id,
                    "selected_device_id": selected_device_id,
                    "devices": _device_rows(selected_device_id),
                },
            }
        )
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/devices/delete", methods=["POST"])
def api_devices_delete() -> tuple[Response, int] | Response:
    body = request.get_json(silent=True) or {}
    try:
        device_id = _managed_device_id(body)
        if device_id == cfg.device_id:
            raise ValueError("default device cannot be deleted")
        storage.remove_managed_device(device_id)
        selected_candidate = str(body.get("selected_device_id") or "").strip()
        if selected_candidate == device_id:
            selected_candidate = ""
        selected_device_id = _resolve_selected_device_id(selected_candidate)
        return jsonify(
            {
                "code": 0,
                "message": "deleted",
                "data": {
                    "device_id": device_id,
                    "selected_device_id": selected_device_id,
                    "devices": _device_rows(selected_device_id),
                },
            }
        )
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/history")
def api_history() -> tuple[Response, int] | Response:
    try:
        limit, device_id, start_ts, end_ts = _history_query_args(cfg.history_limit_default)
        data = storage.recent_telemetry(limit, device_id=device_id, start_ts=start_ts, end_ts=end_ts)
        return jsonify({"code": 0, "message": "ok", "data": data})
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/events")
def api_events() -> Response:
    limit = int(request.args.get("limit", 100))
    device_id = _request_device_id()
    return jsonify({"code": 0, "message": "ok", "data": storage.recent_events(limit, device_id=device_id)})


@app.route("/api/export/telemetry.csv")
def api_export_telemetry_csv() -> tuple[Response, int] | Response:
    try:
        limit, device_id, start_ts, end_ts = _history_query_args(5000)
        csv_text = storage.export_telemetry_csv(limit, device_id=device_id, start_ts=start_ts, end_ts=end_ts)
        return Response(
            csv_text,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=telemetry.csv"},
        )
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/switch", methods=["POST"])
def api_switch() -> tuple[Response, int] | Response:
    body = request.get_json(silent=True) or {}
    try:
        cmd = make_switch_cmd(int(body.get("switch")))
        payload = mqtt_worker.publish_command(cmd, device_id=_request_device_id(body))
        return jsonify({"code": 0, "message": "published", "data": payload})
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/timer", methods=["POST"])
def api_timer() -> tuple[Response, int] | Response:
    body = request.get_json(silent=True) or {}
    try:
        cmd = make_timer_set_cmd(str(body.get("action", "")), int(body.get("delay_s", 0)))
        payload = mqtt_worker.publish_command(cmd, device_id=_request_device_id(body))
        return jsonify({"code": 0, "message": "published", "data": payload})
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/timer/cancel", methods=["POST"])
def api_timer_cancel() -> tuple[Response, int] | Response:
    body = request.get_json(silent=True) or {}
    try:
        payload = mqtt_worker.publish_command(make_timer_cancel_cmd(), device_id=_request_device_id(body))
        return jsonify({"code": 0, "message": "published", "data": payload})
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/timer/query", methods=["POST"])
def api_timer_query() -> tuple[Response, int] | Response:
    body = request.get_json(silent=True) or {}
    try:
        payload = mqtt_worker.publish_command(make_timer_query_cmd(), device_id=_request_device_id(body))
        return jsonify({"code": 0, "message": "published", "data": payload})
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/config", methods=["POST"])
def api_config() -> tuple[Response, int] | Response:
    body = request.get_json(silent=True) or {}
    try:
        cmd = make_config_set_cmd(body)
        payload = mqtt_worker.publish_command(cmd, device_id=_request_device_id(body))
        return jsonify({"code": 0, "message": "published", "data": payload})
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/status/query", methods=["POST"])
def api_status_query() -> tuple[Response, int] | Response:
    body = request.get_json(silent=True) or {}
    try:
        payload = mqtt_worker.publish_command(make_status_query_cmd(), device_id=_request_device_id(body))
        return jsonify({"code": 0, "message": "published", "data": payload})
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/restart", methods=["POST"])
def api_restart() -> tuple[Response, int] | Response:
    body = request.get_json(silent=True) or {}
    try:
        payload = mqtt_worker.publish_command(make_restart_cmd(), device_id=_request_device_id(body))
        return jsonify({"code": 0, "message": "published", "data": payload})
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/api/raw_cmd", methods=["POST"])
def api_raw_cmd() -> tuple[Response, int] | Response:
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    try:
        if not isinstance(data, dict) or "cmd" not in data:
            raise ValueError("raw command body must contain cmd field")
        cmd_data = dict(data)
        cmd_data.pop("device_id", None)
        payload = mqtt_worker.publish_command(cmd_data, device_id=_request_device_id(body))
        return jsonify({"code": 0, "message": "published", "data": payload})
    except Exception as exc:
        return _error(str(exc), 400)


@app.route("/health")
def health() -> Response:
    snap = mqtt_worker.snapshot(cfg.device_id)
    return jsonify({"ok": True, "mqtt_connected": snap["state"].get("mqtt_connected")})


mqtt_worker.start()

if __name__ == "__main__":
    app.run(host=cfg.flask_host, port=cfg.flask_port, debug=cfg.flask_debug, use_reloader=False)
