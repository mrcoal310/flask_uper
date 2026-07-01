import csv
import json
import os
import sqlite3
import threading
import time
from io import StringIO
from typing import Any, Dict, List, Optional


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    device_id TEXT NOT NULL,
                    temperature REAL,
                    humidity REAL,
                    sensor_ok INTEGER,
                    switch_state INTEGER,
                    timer_enable INTEGER,
                    timer_action TEXT,
                    timer_remain_s INTEGER,
                    report_period_s INTEGER,
                    rssi INTEGER,
                    fw_ver TEXT,
                    raw TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(ts);
                CREATE INDEX IF NOT EXISTS idx_telemetry_device_ts ON telemetry(device_id, ts);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    device_id TEXT NOT NULL,
                    msg_type TEXT NOT NULL,
                    level TEXT,
                    event_type TEXT,
                    req_id TEXT,
                    raw TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
                CREATE INDEX IF NOT EXISTS idx_events_device_ts ON events(device_id, ts);

                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_devices_deleted_updated
                ON devices(deleted, updated_ts DESC, device_id ASC);
                """
            )
            self._ensure_column("telemetry", "timer_action", "TEXT")
            self._ensure_column("telemetry", "rssi", "INTEGER")
            self._ensure_column("telemetry", "fw_ver", "TEXT")
            self._conn.commit()

    def _ensure_column(self, table: str, column: str, column_type: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]) for row in rows}
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def insert_telemetry(self, payload: Dict[str, Any]) -> None:
        data = payload.get("data") or {}
        ext = payload.get("ext") or {}
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO telemetry (
                    ts, device_id, temperature, humidity, sensor_ok,
                    switch_state, timer_enable, timer_action, timer_remain_s,
                    report_period_s, rssi, fw_ver, raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _payload_timestamp(payload),
                    str(payload.get("device_id") or ""),
                    _float_or_none(data.get("temperature")),
                    _float_or_none(data.get("humidity")),
                    _bool_int(data.get("sensor_ok")),
                    _int_or_none(data.get("switch")),
                    _bool_int(data.get("timer_enable")),
                    _str_or_none(data.get("timer_action")),
                    _int_or_none(data.get("timer_remain_s")),
                    _int_or_none(data.get("report_period_s")),
                    _int_or_none(data.get("rssi")),
                    _str_or_none(ext.get("fw_ver") or data.get("fw_ver")),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def insert_event(self, payload: Dict[str, Any], msg_type: str) -> None:
        data = payload.get("data") or {}
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO events (ts, device_id, msg_type, level, event_type, req_id, raw)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _payload_timestamp(payload),
                    str(payload.get("device_id") or ""),
                    msg_type,
                    str(data.get("level") or ""),
                    str(data.get("event_type") or data.get("cmd") or msg_type),
                    str(payload.get("req_id") or ""),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def recent_telemetry(
        self,
        limit: int = 200,
        device_id: Optional[str] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        sql = """
            SELECT ts, device_id, temperature, humidity, sensor_ok, switch_state,
                   timer_enable, timer_action, timer_remain_s, report_period_s,
                   rssi, fw_ver
            FROM telemetry
        """
        where_sql, params = self._telemetry_filters(device_id=device_id, start_ts=start_ts, end_ts=end_ts)
        sql += where_sql
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def recent_events(self, limit: int = 100, device_id: Optional[str] = None) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        sql = """
            SELECT ts, device_id, msg_type, level, event_type, req_id, raw
            FROM events
        """
        params: list[Any] = []
        if device_id:
            sql += " WHERE device_id = ?"
            params.append(str(device_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("raw"))
            except Exception:
                pass
            out.append(item)
        return out

    def export_telemetry_csv(
        self,
        limit: int = 5000,
        device_id: Optional[str] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> str:
        rows = self.recent_telemetry(limit, device_id=device_id, start_ts=start_ts, end_ts=end_ts)
        buf = StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=[
                "ts",
                "device_id",
                "temperature",
                "humidity",
                "sensor_ok",
                "switch_state",
                "timer_enable",
                "timer_action",
                "timer_remain_s",
                "report_period_s",
                "rssi",
                "fw_ver",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue()

    def _telemetry_filters(
        self,
        device_id: Optional[str] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if device_id:
            clauses.append("device_id = ?")
            params.append(str(device_id))
        if start_ts is not None:
            clauses.append("ts >= ?")
            params.append(int(start_ts))
        if end_ts is not None:
            clauses.append("ts <= ?")
            params.append(int(end_ts))
        if not clauses:
            return "", params
        return f" WHERE {' AND '.join(clauses)}", params

    def add_managed_device(self, device_id: str) -> str:
        text = _normalized_device_id(device_id)
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO devices (device_id, created_ts, updated_ts, deleted)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(device_id) DO UPDATE SET
                    updated_ts = excluded.updated_ts,
                    deleted = 0
                """,
                (text, now, now),
            )
            self._conn.commit()
        return text

    def remove_managed_device(self, device_id: str) -> str:
        text = _normalized_device_id(device_id)
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO devices (device_id, created_ts, updated_ts, deleted)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(device_id) DO UPDATE SET
                    updated_ts = excluded.updated_ts,
                    deleted = 1
                """,
                (text, now, now),
            )
            self._conn.commit()
        return text

    def managed_device_ids(self, limit: int = 200) -> List[str]:
        return self._device_ids_by_deleted(0, limit)

    def deleted_device_ids(self, limit: int = 500) -> List[str]:
        return self._device_ids_by_deleted(1, limit)

    def known_device_ids(self, limit: int = 100) -> List[str]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT device_id
                FROM (
                    SELECT device_id, MAX(ts) AS last_ts
                    FROM telemetry
                    WHERE device_id <> ''
                    GROUP BY device_id
                    UNION ALL
                    SELECT device_id, MAX(ts) AS last_ts
                    FROM events
                    WHERE device_id <> ''
                    GROUP BY device_id
                ) AS combined
                GROUP BY device_id
                ORDER BY MAX(last_ts) DESC, device_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["device_id"]) for row in rows if row["device_id"]]

    def _device_ids_by_deleted(self, deleted: int, limit: int) -> List[str]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT device_id
                FROM devices
                WHERE deleted = ?
                ORDER BY updated_ts DESC, device_id ASC
                LIMIT ?
                """,
                (int(deleted), limit),
            ).fetchall()
        return [str(row["device_id"]) for row in rows if row["device_id"]]


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _bool_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes", "on"} else 0
    return None


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_device_id(value: Any) -> str:
    text = _str_or_none(value)
    if text is None:
        raise ValueError("device_id is required")
    if len(text) > 128:
        raise ValueError("device_id is too long")
    return text


def _payload_timestamp(payload: Dict[str, Any]) -> int:
    raw = payload.get("timestamp")
    try:
        ts = int(raw)
    except Exception:
        return int(time.time())

    # Device-side demo values like 7200 are not valid Unix timestamps for charts/history.
    if ts < 946684800:
        return int(time.time())
    return ts
