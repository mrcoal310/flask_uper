# 基于 Flask 的物联网上位机

这是物联网项目中基于 Flask 的上位机部分，用于通过 MQTT 与服务器通信，并提供 Web 页面进行设备监控与控制。

## 实现的功能

- 实时显示设备在线状态、温湿度、开关状态、定时器状态、RSSI、固件版本
- 下发控制命令：开关、定时、查询状态、重启设备、配置更新
- 保存历史遥测数据到 SQLite
- 查看最近消息 / 事件
- 导出遥测历史为 CSV

## 目录结构

```text
flask_uper/
├── app.py
├── config.py
├── mqtt_worker.py
├── protocol.py
├── storage.py
├── MQTT_PROTOCOL.md
├── pyproject.toml
├── requirements.txt
├── uv.lock
├── .env
├── templates/
│   └── index.html
└── static/
    ├── app.js
    └── style.css
```

## 环境配置

项目使用 uv 管理 Python 环境和依赖：

```bash
uv sync
```

如果不使用 uv，也可以通过 `requirements.txt` 安装依赖：

```bash
pip install -r requirements.txt
```

## 启动方式

使用 uv 启动：

```bash
uv run python app.py
```

或在已安装依赖的 Python 环境中直接运行：

```bash
python app.py
```

## 监测地址

启动后通过浏览器访问：

```text
http://127.0.0.1:5000
```

## 配置信息

- `.env` 文件用于存放 Flask 服务地址、MQTT 连接信息以及 SQLite 数据库文件地址
- `MQTT_PROTOCOL.md` 文件记录了设备与上位机通信时使用的 JSON 数据格式
