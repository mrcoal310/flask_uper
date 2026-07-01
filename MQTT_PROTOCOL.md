# MQTT 通信格式说明

本文档根据当前项目代码整理，主要对应 `config.py`、`protocol.py`、`mqtt_worker.py` 和 `app.py` 中已经实现的 MQTT 通信逻辑。系统使用 Flask 上位机通过 MQTT Broker 与设备通信，上位机接收设备上报的状态、遥测、事件消息，并向设备下发控制命令。

# 1. 通信基础

主要环境变量如下：

| 环境变量 | 作用 | 代码默认值 |
| --- | --- | --- |
| `MQTT_HOST` / `MQTT_HOST_URL` | MQTT Broker 地址 | `127.0.0.1` |
| `MQTT_PORT`                   | MQTT Broker 端口 | `1883` |
| `MQTT_CLIENT_ID`              | 上位机 MQTT 客户端 ID | 未配置时自动生成 |
| `MQTT_USERNAME`               | MQTT 用户名 | 按服务器上提供用户名 |
| `MQTT_PASSWORD`               | MQTT 密码 | 按服务器上提供密码 |
| `MQTT_KEEPALIVE`              | MQTT 保活时间，单位秒 | 默认`30` |
| `MQTT_TLS`                    | 是否启用 TLS | 默认`false` |
| `APP_PRODUCT_ID`              | 产品 ID | 默认`envctrl_v1` |
| `APP_DEVICE_ID`               | 设备 ID | 默认`iot_node_001` |
| `APP_PROTO_VER`               | 协议版本 | `1.0` |
| `MQTT_RECEIVE_TOPIC`          | 上位机接收主题 | 当前为`/k0xzrztwuSU/Android/user/DATA` |
| `MQTT_SEND_TOPIC`             | 上位机下发主题 | 当前为`/k0xzrztwuSU/Android/user/SEETING` |
| `MQTT_BASE_TOPIC`             | 自定义基础主题 | 保留 |

# 2. Topic 规则

上位机使用固定收发 Topic

| `/k0xzrztwuSU/Android/user/DATA`    | 设备数据上报主题 |
| `/k0xzrztwuSU/Android/user/SEETING` | 上位机命令下发主题 |


# 3. 统一 JSON 信封

上位机下发命令时，会统一封装为以下 JSON 结构。设备上报消息也建议使用同样结构。

```json
{
  "proto_ver": "1.0",
  "product_id": "envctrl_v1",
  "device_id": "iot_node_001",
  "msg_type": "telemetry",
  "req_id": "",
  "seq": 0,
  "timestamp": 1717560000,
  "data": {},
  "error": null,
  "ext": {}
}
```

字段说明：

| `proto_ver`  | string      | 协议版本，默认 `1.0` |
| `product_id` | string      | 产品 ID，默认 `envctrl_v1` |
| `device_id`  | string      | 设备 ID；缺省时上位机使用默认设备 ID |
| `msg_type`   | string      | 消息类型 |
| `req_id`     | string      | 请求 ID；命令下发时由上位机自动生成 |
| `seq`        | number      | 消息序号；命令下发时由上位机递增生成 |
| `timestamp`  | number      | Unix 时间戳 |
| `data`       | object      | 业务数据 |
| `error`      | object/null | 错误信息，没有错误时为 `null` |
| `ext`        | object      | 扩展字段，如固件版本、来源等 |

兼容规则：

· 如果设备上报的是完整信封，并且包含 `msg_type` 与对象类型的 `data`，上位机会直接使用该结构
· 如果设备上报的是普通 JSON 对象，上位机会把非保留字段整理进 `data`
· 如果设备上报的不是 JSON，上位机会记录为 `raw` 消息，原始文本放入 `data.raw`
· 如果 Topic 后缀为 `DATA`，上位机会推断 `msg_type=telemetry`

# 4. 设备上报消息

## 4.1 遥测消息 telemetry

用于设备周期性上报温湿度、开关、定时器、RSSI 等实时数据。该消息会写入 SQLite 的 `telemetry` 表

```json
{
  "proto_ver": "1.0",
  "product_id": "envctrl_v1",
  "device_id": "iot_node_001",
  "msg_type": "telemetry",
  "req_id": "",
  "seq": 12,
  "timestamp": 1717560000,
  "data": {
    "temperature": 26.5,
    "humidity": 61.2,
    "sensor_ok": true,
    "switch": 1,
    "timer_enable": 0,
    "timer_action": "",
    "timer_remain_s": 0,
    "report_period_s": 5,
    "rssi": -58
  },
  "error": null,
  "ext": {
    "fw_ver": "1.0.0"
  }
}
```

`data` 字段说明：

| `temperature`     | number         | 温度，单位摄氏度 |
| `humidity`        | number         | 湿度，单位 `%RH` |
| `sensor_ok`       | boolean/number | 传感器状态，真值表示正常 |
| `switch`          | number         | 开关状态，`1` 表示开启，`0` 表示关闭 |
| `timer_enable`    | boolean/number | 定时器是否启用 |
| `timer_action`    | string         | 定时动作，可为 `on` / `off` |
| `timer_remain_s`  | number         | 定时剩余秒数 |
| `report_period_s` | number         | 上报周期，单位秒 |
| `rssi`            | number         | 信号强度，单位 dBm |
| `fw_ver`          | string         | 固件版本 |

## 4.2 状态消息 status

用于设备主动上报或响应状态查询。上位机会更新实时状态，并将该消息写入 `events` 表

```json
{
  "proto_ver": "1.0",
  "product_id": "envctrl_v1",
  "device_id": "iot_node_001",
  "msg_type": "status",
  "req_id": "pc-20240605-120000-a1b2c3d4",
  "seq": 13,
  "timestamp": 1717560005,
  "data": {
    "online": true,
    "switch": 1,
    "report_period_s": 5,
    "timer_enable": 0,
    "rssi": -58
  },
  "error": null,
  "ext": {
    "fw_ver": "1.0.0"
  }
}
```

## 4.3 心跳消息 heartbeat

用于表示设备在线。上位机会更新 `last_heartbeat`、`last_seen_ts` 和在线状态

```json
{
  "proto_ver": "1.0",
  "product_id": "envctrl_v1",
  "device_id": "iot_node_001",
  "msg_type": "heartbeat",
  "req_id": "",
  "seq": 14,
  "timestamp": 1717560010,
  "data": {
    "online": true,
    "rssi": -60
  },
  "error": null,
  "ext": {}
}
```

## 4.4 命令回复 reply

用于设备回复上位机下发的命令

```json
{
  "proto_ver": "1.0",
  "product_id": "envctrl_v1",
  "device_id": "iot_node_001",
  "msg_type": "reply",
  "req_id": "pc-20240605-120000-a1b2c3d4",
  "seq": 15,
  "timestamp": 1717560012,
  "data": {
    "cmd": "switch_set",
    "result": "ok",
    "switch": 1
  },
  "error": null,
  "ext": {}
}
```

## 4.5 事件消息 event

用于设备上报告警、配置变化、异常等事件，上位机会写入 `events` 表

```json
{
  "proto_ver": "1.0",
  "product_id": "envctrl_v1",
  "device_id": "iot_node_001",
  "msg_type": "event",
  "req_id": "",
  "seq": 16,
  "timestamp": 1717560020,
  "data": {
    "level": "warn",
    "event_type": "temp_high",
    "message": "temperature is higher than limit"
  },
  "error": null,
  "ext": {}
}
```

## 4.6 遗嘱消息 lwt

用于设备离线通知

```json
{
  "proto_ver": "1.0",
  "product_id": "envctrl_v1",
  "device_id": "iot_node_001",
  "msg_type": "lwt",
  "req_id": "",
  "seq": 0,
  "timestamp": 1717560030,
  "data": {
    "online": false,
    "event_type": "offline",
    "message": "device disconnected"
  },
  "error": null,
  "ext": {}
}
```

# 5. 上位机下发命令

上位机通过 API 接收用户操作后，会把命令封装为 `msg_type=cmd` 的 MQTT 消息，并发布到命令 Topic

命令信封示例：

```json
{
  "proto_ver": "1.0",
  "product_id": "envctrl_v1",
  "device_id": "iot_node_001",
  "msg_type": "cmd",
  "req_id": "pc-20240605-120000-a1b2c3d4",
  "seq": 1,
  "timestamp": 1717560100,
  "data": {
    "cmd": "switch_set",
    "switch": 1
  },
  "error": null,
  "ext": {
    "source": "flask_upper_computer"
  }
}
```

已实现命令列表：

| `POST /api/switch`       | `switch_set`   | `switch`            | `0` 或 `1` |
| `POST /api/timer`        | `timer_set`    | `action`, `delay_s` | `action` 为 `on` / `off`；`delay_s` 为 `1 - 86400` |
| `POST /api/timer/cancel` | `timer_cancel` |
| `POST /api/timer/query`  | `timer_query`  | 
| `POST /api/status/query` | `status_query` | 
| `POST /api/restart`      | `restart`      | 
| `POST /api/config`       | `config_set`   | 至少包含一个有效配置项 |
| `POST /api/raw_cmd`      | `default`      | `cmd` 必填 | 

`config`命令支持字段：

| `report_period_s`     | number  | `2..3600` |
| `temp_high_limit`     | number  | `-20..80` |
| `humidity_high_limit` | number  | `0..100` |
| `auto_rule_enable`    | boolean | `true` / `false` |
| `web_token`           | string  |

配置命令示例：

```json
{
  "proto_ver": "1.0",
  "product_id": "envctrl_v1",
  "device_id": "iot_node_001",
  "msg_type": "cmd",
  "req_id": "pc-20240605-120030-b2c3d4e5",
  "seq": 2,
  "timestamp": 1717560130,
  "data": {
    "cmd": "config_set",
    "report_period_s": 5,
    "temp_high_limit": 30,
    "humidity_high_limit": 80,
    "auto_rule_enable": true
  },
  "error": null,
  "ext": {
    "source": "flask_upper_computer"
  }
}
```