# 基于 Flask 的物联网上位机

这是物联网项目中基于 Flask 的上位机部分，用于通过 MQTT 与服务器通信，并提供 Web 页面进行设备监控与控制

# 实现的功能

· 实时显示设备在线状态、温湿度、开关状态、定时器状态、RSSI、固件版本
· 下发控制命令：开关、定时、查询状态、重启设备、配置更新
· 保存历史遥测数据到 SQLite
· 查看最近消息 / 事件
· 导出遥测历史为 CSV

# 目录结构

flask_iot_upper_computer/
├── app.py
├── config.py
├── mqtt_worker.py
├── protocol.py
├── storage.py
├── MQTT_PROTOCOL.md
├── requirements.txt
├── .env
├── templates/
│   └── index.html
├── static/
│   ├── app.js
│   └── style.css

# 环境配置

采用UV配置

# 启动方式

运行 python app.py

# 监测地址

启动后通过浏览器访问：http://127.0.0.1:5000

# 配置信息

.env 文件中存放了上位机 Flask 前端地址、MQTT 配置信息以及存储数据库文件地址
MQTT_PROTOCOL.md 文件中存放了具体的 JSON 格式