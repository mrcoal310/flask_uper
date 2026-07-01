const defaultDeviceId = (window.APP_CONFIG && window.APP_CONFIG.defaultDeviceId) || "";
const currentProductId = (window.APP_CONFIG && window.APP_CONFIG.productId) || "";
const DEFAULT_HISTORY_LIMIT = 120;
const FILTERED_HISTORY_LIMIT = 5000;

let historyChart = null;
let cachedLiveMessages = [];
let cachedStoredEvents = [];
let cachedDeviceRows = [];
let chartResizeTimer = null;
let currentDeviceId = defaultDeviceId;
let lastHistorySignature = "";
let historyFilters = {
  startAt: "",
  endAt: ""
};

function buildApiUrl(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });
  return `${url.pathname}${url.search}`;
}

function setCommandFeedback(state, title, text) {
  const box = document.getElementById("command-feedback");
  const titleEl = document.getElementById("command-feedback-title");
  const badgeEl = document.getElementById("command-feedback-badge");
  const textEl = document.getElementById("command-feedback-text");
  if (!box || !titleEl || !badgeEl || !textEl) return;

  box.className = `command-feedback is-${state}`;
  titleEl.textContent = title;
  badgeEl.textContent = {
    idle: "就绪",
    pending: "进行中",
    success: "已完成",
    error: "失败"
  }[state] || "状态";
  textEl.textContent = text;
}

function setRawCommand(value) {
  const pre = document.getElementById("last-command");
  if (!pre) return;
  pre.textContent = value;
}

function setCount(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = String(value);
}

function fmtTs(ts) {
  if (!ts) return "--";
  return new Date(ts * 1000).toLocaleString();
}

function setText(id, value, klass) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
  if (klass !== undefined) el.className = klass;
}

async function apiGet(path, params = {}) {
  const response = await fetch(buildApiUrl(path, params));
  const data = await response.json();
  if (!response.ok || data.code !== 0) {
    throw new Error(data.message || "request failed");
  }
  return data;
}

async function apiPost(path, body = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok || data.code !== 0) {
    throw new Error(data.message || "request failed");
  }
  return data;
}

function withDevice(body = {}) {
  return { ...body, device_id: currentDeviceId };
}

function refreshActiveDeviceLabel() {
  const el = document.getElementById("active-device-label");
  if (!el) return;
  el.textContent = currentProductId ? `${currentProductId}/${currentDeviceId}` : currentDeviceId;
}

function updateExportLink() {
  const el = document.getElementById("export-link");
  if (!el) return;
  el.href = buildApiUrl("/api/export/telemetry.csv", {
    limit: 5000,
    device_id: currentDeviceId,
    start_at: historyFilters.startAt,
    end_at: historyFilters.endAt
  });
}

function readHistoryFiltersFromInputs() {
  const startInput = document.getElementById("history-start-at");
  const endInput = document.getElementById("history-end-at");
  historyFilters = {
    startAt: String((startInput && startInput.value) || "").trim(),
    endAt: String((endInput && endInput.value) || "").trim()
  };
  updateExportLink();
}

function clearHistoryFilters() {
  historyFilters = { startAt: "", endAt: "" };
  const startInput = document.getElementById("history-start-at");
  const endInput = document.getElementById("history-end-at");
  if (startInput) startInput.value = "";
  if (endInput) endInput.value = "";
  updateExportLink();
}

function hasActiveHistoryFilters() {
  return Boolean(historyFilters.startAt || historyFilters.endAt);
}

function getHistoryRequestLimit() {
  return hasActiveHistoryFilters() ? FILTERED_HISTORY_LIMIT : DEFAULT_HISTORY_LIMIT;
}

function normalizeReadingValue(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isNaN(numeric) ? String(value) : numeric;
}

function isSameReading(left, right) {
  if (!left || !right) return false;
  return (
    normalizeReadingValue(left.temperature) === normalizeReadingValue(right.temperature) &&
    normalizeReadingValue(left.humidity) === normalizeReadingValue(right.humidity)
  );
}

function compressHistoryRows(rows, options = {}) {
  const keepTrailingFlatPoint = Boolean(options.keepTrailingFlatPoint);
  if (!Array.isArray(rows) || rows.length <= 2) {
    return Array.isArray(rows) ? rows.slice() : [];
  }

  const compressed = [rows[0]];
  for (let index = 1; index < rows.length - 1; index += 1) {
    if (!isSameReading(rows[index], rows[index - 1])) {
      compressed.push(rows[index]);
    }
  }

  const lastRow = rows[rows.length - 1];
  const lastKeptRow = compressed[compressed.length - 1];
  if (lastRow && lastKeptRow && lastRow.ts !== lastKeptRow.ts) {
    if (keepTrailingFlatPoint || !isSameReading(lastRow, lastKeptRow)) {
      compressed.push(lastRow);
    }
  }

  if (keepTrailingFlatPoint && compressed.length === 1 && rows.length > 1) {
    return [rows[0], lastRow];
  }

  return compressed;
}

function buildHistorySignature(rows) {
  return rows
    .map((row) => [
      normalizeReadingValue(row.temperature),
      normalizeReadingValue(row.humidity)
    ].join("|"))
    .join(";");
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[char]));
}

function updateDeviceSwitchSummary(devices = cachedDeviceRows, selectedDeviceId = currentDeviceId) {
  const nameEl = document.getElementById("device-switch-current-name");
  const statusEl = document.getElementById("device-switch-current-status");
  const inlineNameEl = document.getElementById("device-current-inline");
  if (!nameEl || !statusEl) return;

  const rows = Array.isArray(devices) ? devices : [];
  const current = rows.find((item) => item.device_id === selectedDeviceId);
  const deviceName = (current && current.device_id) || selectedDeviceId || "--";
  const online = Boolean(current && current.online);

  nameEl.textContent = deviceName;
  if (inlineNameEl) inlineNameEl.textContent = deviceName;
  statusEl.textContent = online ? "在线" : "离线";
  statusEl.className = `device-switch-status ${online ? "is-online" : "is-offline"}`;
}

function renderDeviceSwitchList(devices = cachedDeviceRows, selectedDeviceId = currentDeviceId) {
  const listEl = document.getElementById("device-switch-list");
  if (!listEl) return;

  const rows = Array.isArray(devices) ? devices : [];
  listEl.innerHTML = "";

  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "device-switch-empty";
    empty.textContent = "当前还没有可切换设备。";
    listEl.appendChild(empty);
    return;
  }

  rows.forEach((item) => {
    const isSelected = item.device_id === selectedDeviceId;
    const row = document.createElement("div");
    row.className = `device-switch-item ${isSelected ? "is-selected" : ""}`;

    const badges = [];
    badges.push(`<span class="device-badge ${item.online ? "is-online" : "is-offline"}">${item.online ? "在线" : "离线"}</span>`);
    if (isSelected) {
      badges.push('<span class="device-badge is-current">当前</span>');
    }
    if (item.removable === false) {
      badges.push('<span class="device-badge is-default">默认</span>');
    }

    row.innerHTML = `
      <button
        type="button"
        class="device-switch-pick"
        data-device-id="${escapeHtml(item.device_id)}"
        ${isSelected ? "disabled" : ""}
      >
        <span class="device-switch-item-main">
          <strong class="device-item-id">${escapeHtml(item.device_id)}</strong>
          <span class="device-item-meta">
            ${badges.join("")}
            <span class="device-item-time">最后在线：${item.last_seen_ts ? fmtTs(item.last_seen_ts) : "--"}</span>
          </span>
        </span>
      </button>
      <div class="device-switch-actions">
        <button
          type="button"
          class="danger-button device-delete-btn"
          data-device-id="${escapeHtml(item.device_id)}"
          ${item.removable === false ? "disabled" : ""}
        >删除</button>
      </div>
    `;
    listEl.appendChild(row);
  });
}

function renderDeviceOptions(devices = [], selectedDeviceId = currentDeviceId) {
  const select = document.getElementById("device-select");

  const rows = Array.isArray(devices) ? [...devices] : [];
  if (selectedDeviceId && !rows.some((item) => item.device_id === selectedDeviceId)) {
    rows.unshift({
      device_id: selectedDeviceId,
      online: false,
      last_seen_ts: null,
      removable: selectedDeviceId !== defaultDeviceId
    });
  }

  cachedDeviceRows = rows;

  if (select) {
    select.innerHTML = "";

    rows.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.device_id;
      option.textContent = `${item.device_id} (${item.online ? "在线" : "离线"})`;
      option.selected = item.device_id === selectedDeviceId;
      select.appendChild(option);
    });

    if (!select.value && selectedDeviceId) {
      select.value = selectedDeviceId;
    }
  }

  renderDeviceSwitchList(rows, selectedDeviceId);
}

function renderManagedDeviceList(devices = cachedDeviceRows) {
  const rows = Array.isArray(devices) ? devices : [];
  setCount("device-count", rows.length);

  const listEl = document.getElementById("device-manage-list");
  if (!listEl) return;

  listEl.innerHTML = "";

  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "device-manage-empty";
    empty.textContent = "当前还没有设备记录。";
    listEl.appendChild(empty);
    return;
  }

  rows.forEach((item) => {
    const row = document.createElement("div");
    row.className = "device-item";

    const badges = [];
    badges.push(`<span class="device-badge ${item.online ? "is-online" : "is-offline"}">${item.online ? "在线" : "离线"}</span>`);
    if (item.device_id === currentDeviceId) {
      badges.push('<span class="device-badge is-current">当前</span>');
    }
    if (item.removable === false) {
      badges.push('<span class="device-badge is-default">默认</span>');
    }

    row.innerHTML = `
      <div class="device-item-main">
        <strong class="device-item-id">${escapeHtml(item.device_id)}</strong>
        <div class="device-item-meta">
          ${badges.join("")}
          <span class="device-item-time">最后在线：${item.last_seen_ts ? fmtTs(item.last_seen_ts) : "--"}</span>
        </div>
      </div>
      <div class="device-item-actions">
        <button
          type="button"
          class="danger-button device-delete-btn"
          data-device-id="${escapeHtml(item.device_id)}"
          ${item.removable === false ? "disabled" : ""}
        >删除</button>
      </div>
    `;
    listEl.appendChild(row);
  });
}

function applyDevicePayload(payload = {}, fallbackDeviceId = currentDeviceId) {
  const data = Array.isArray(payload)
    ? { devices: payload, selected_device_id: fallbackDeviceId }
    : (payload || {});

  currentDeviceId = data.selected_device_id || fallbackDeviceId || currentDeviceId || defaultDeviceId;
  refreshActiveDeviceLabel();
  updateExportLink();
  renderDeviceOptions(data.devices || [], currentDeviceId);
  updateDeviceSwitchSummary(data.devices || [], currentDeviceId);
  renderManagedDeviceList(data.devices || []);
}

async function refreshStatus() {
  try {
    const res = await apiGet("/api/status", { device_id: currentDeviceId });
    const data = res.data || {};
    const st = data.state || {};

    applyDevicePayload(data, currentDeviceId);

    setText("mqtt-connected", st.mqtt_connected ? "已连接" : "未连接", st.mqtt_connected ? "ok" : "bad");
    setText("device-online", st.online ? "在线" : "离线", st.online ? "ok" : "bad");
    setText("temperature", st.temperature === null || st.temperature === undefined ? "--" : `${Number(st.temperature).toFixed(1)} °C`);
    setText("humidity", st.humidity === null || st.humidity === undefined ? "--" : `${Number(st.humidity).toFixed(1)} %RH`);
    setText("sensor-ok", st.sensor_ok === null || st.sensor_ok === undefined ? "--" : (st.sensor_ok ? "正常" : "异常"), st.sensor_ok ? "ok" : "bad");
    setText("switch-state", st.switch === null || st.switch === undefined ? "--" : (Number(st.switch) ? "已开启" : "已关闭"));
    setText("timer-enable", st.timer_enable === null || st.timer_enable === undefined ? "--" : (st.timer_enable ? "已启用" : "未启用"));
    setText(
      "timer-action-state",
      st.timer_action === null || st.timer_action === undefined || st.timer_action === ""
        ? "--"
        : (
          String(st.timer_action) === "1" || st.timer_action === "on"
            ? "延时开启"
            : (String(st.timer_action) === "0" || st.timer_action === "off" ? "延时关闭" : String(st.timer_action))
        )
    );
    setText("timer-remain", st.timer_remain_s === null || st.timer_remain_s === undefined ? "--" : `${st.timer_remain_s} s`);
    setText("report-period-state", st.report_period_s === null || st.report_period_s === undefined ? "--" : `${st.report_period_s} s`);
    setText("rssi-state", st.rssi === null || st.rssi === undefined ? "--" : `${st.rssi} dBm`);
    setText("fw-ver-state", st.fw_ver === null || st.fw_ver === undefined || st.fw_ver === "" ? "--" : String(st.fw_ver));
    setText("last-seen", fmtTs(st.last_seen_ts));

    const topics = data.topics || {};
    const topicPre = document.getElementById("topic-list");
    if (topicPre) topicPre.textContent = JSON.stringify(topics, null, 2);
    setCount("topic-count", Object.keys(topics).filter((key) => topics[key]).length);

    cachedLiveMessages = data.recent_messages || [];
    updateMessagePanel();
  } catch (error) {
    console.error(error);
    setText("mqtt-connected", "无法连接", "bad");
  }
}

async function refreshDevices() {
  try {
    const res = await apiGet("/api/devices", { device_id: currentDeviceId });
    applyDevicePayload(res.data || {}, currentDeviceId);
  } catch (error) {
    console.error(error);
  }
}

async function refreshHistory(options = {}) {
  const force = Boolean(options.force);
  if (!force && hasActiveHistoryFilters()) {
    return;
  }

  try {
    const res = await apiGet("/api/history", {
      limit: getHistoryRequestLimit(),
      device_id: currentDeviceId,
      start_at: historyFilters.startAt,
      end_at: historyFilters.endAt
    });
    const rows = Array.isArray(res.data) ? res.data : [];
    const drawRows = compressHistoryRows(rows, { keepTrailingFlatPoint: force });
    const signatureRows = compressHistoryRows(rows, { keepTrailingFlatPoint: false });
    const nextSignature = buildHistorySignature(signatureRows);

    if (!force && nextSignature === lastHistorySignature) {
      return;
    }

    lastHistorySignature = nextSignature;
    drawChart(drawRows);
  } catch (error) {
    console.error(error);
    setCommandFeedback("error", "历史数据查询失败", `无法获取温湿度历史：${error.message}`);
  }
}

async function refreshEvents() {
  try {
    const res = await apiGet("/api/events", { limit: 50, device_id: currentDeviceId });
    const rows = Array.isArray(res.data) ? res.data : [];
    cachedStoredEvents = rows.map((item) => ({
      ts: item.ts,
      msg_type: item.msg_type || "event",
      topic: item.event_type || item.device_id || "history",
      payload: item.payload || item
    }));
    updateMessagePanel();
  } catch (error) {
    console.error(error);
  }
}

function drawChart(rows) {
  const labels = rows.map((row) => new Date(row.ts * 1000).toLocaleString());
  const temp = rows.map((row) => row.temperature);
  const humi = rows.map((row) => row.humidity);
  const ctx = document.getElementById("history-chart");
  if (!ctx) return;

  if (!historyChart) {
    historyChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "温度 °C", data: temp, tension: 0.25 },
          { label: "湿度 %RH", data: humi, tension: 0.25 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: { y: { beginAtZero: false } }
      }
    });
    return;
  }

  historyChart.data.labels = labels;
  historyChart.data.datasets[0].data = temp;
  historyChart.data.datasets[1].data = humi;
  historyChart.update("none");
}

async function submitHistoryQuery() {
  readHistoryFiltersFromInputs();
  if (historyFilters.startAt && historyFilters.endAt && historyFilters.startAt > historyFilters.endAt) {
    setCommandFeedback("error", "时间范围无效", "开始时间不能晚于结束时间");
    return;
  }
  await refreshHistory({ force: true });
}

async function resetHistoryQuery() {
  clearHistoryFilters();
  await refreshHistory({ force: true });
}

function requestChartResize() {
  if (!historyChart) return;
  if (chartResizeTimer) clearTimeout(chartResizeTimer);
  chartResizeTimer = setTimeout(() => {
    historyChart.resize();
    historyChart.update("none");
  }, 80);
}

function renderMessages(messages) {
  const box = document.getElementById("message-list");
  if (!box) return;
  box.innerHTML = "";

  if (!messages.length) {
    const div = document.createElement("div");
    div.className = "message";
    div.innerHTML = `
      <div class="message-header">
        <span>暂无消息</span>
        <span>等待设备上报</span>
      </div>
      <code>当前设备还没有实时消息或历史事件，收到 MQTT 数据后这里会自动刷新。</code>
    `;
    box.appendChild(div);
    return;
  }

  messages.slice(0, 50).forEach((message) => {
    const div = document.createElement("div");
    div.className = "message";
    div.innerHTML = `
      <div class="message-header">
        <span>${fmtTs(message.ts)} | ${message.msg_type}</span>
        <span>${message.topic || ""}</span>
      </div>
      <code>${escapeHtml(JSON.stringify(message.payload, null, 2))}</code>
    `;
    box.appendChild(div);
  });
}

function updateMessagePanel() {
  const messages = cachedLiveMessages.length ? cachedLiveMessages : cachedStoredEvents;
  setCount("message-count", messages.length);
  renderMessages(messages);
}

async function publishAndShow(url, body, meta = {}) {
  setCommandFeedback(
    "pending",
    meta.pendingTitle || "正在发送指令",
    meta.pendingText || "命令已提交，等待设备响应"
  );
  setRawCommand("等待命令发送...");

  try {
    const res = await apiPost(url, withDevice(body));
    setRawCommand(JSON.stringify(res.data, null, 2));
    setCommandFeedback(
      "success",
      meta.successTitle || "指令已发送",
      meta.successText || "命令已成功下发"
    );
    await refreshStatus();
  } catch (error) {
    setRawCommand(`Error: ${error.message}`);
    setCommandFeedback(
      "error",
      meta.errorTitle || "发送失败",
      meta.errorText || `命令发送失败：${error.message}`
    );
  }
}

function setSwitch(value) {
  const isOn = Number(value) === 1;
  publishAndShow("/api/switch", { switch: value }, {
    pendingTitle: isOn ? "正在开启设备" : "正在关闭设备",
    pendingText: isOn ? "开机指令已提交，正在等待设备响应" : "关机指令已提交，正在等待设备响应",
    successTitle: isOn ? "开启指令已发送" : "关闭指令已发送",
    successText: isOn ? "设备开启命令已下发" : "设备关闭命令已下发"
  });
}

function setTimer() {
  const action = document.getElementById("timer-action").value;
  const delayS = Number(document.getElementById("timer-delay").value || 0);
  const actionLabel = action === "on" ? "延时开启" : "延时关闭";
  publishAndShow("/api/timer", { action, delay_s: delayS }, {
    pendingTitle: `正在设置${actionLabel}`,
    pendingText: `${actionLabel}命令已提交，倒计时为 ${delayS} 秒`,
    successTitle: `${actionLabel}已发送`,
    successText: `${actionLabel}设置已下发，倒计时为 ${delayS} 秒`
  });
}

function cancelTimer() {
  publishAndShow("/api/timer/cancel", {}, {
    pendingTitle: "正在取消定时",
    pendingText: "取消定时命令已提交",
    successTitle: "取消指令已发送",
    successText: "取消定时命令已下发"
  });
}

function queryTimer() {
  publishAndShow("/api/timer/query", {}, {
    pendingTitle: "正在查询定时状态",
    pendingText: "查询命令已提交，等待设备上报",
    successTitle: "查询指令已发送",
    successText: "定时查询命令已下发"
  });
}

function queryStatus() {
  publishAndShow("/api/status/query", {}, {
    pendingTitle: "正在查询设备状态",
    pendingText: "状态查询命令已提交",
    successTitle: "查询指令已发送",
    successText: "设备状态查询命令已下发"
  });
}

function restartDevice() {
  publishAndShow("/api/restart", {}, {
    pendingTitle: "正在重启设备",
    pendingText: "重启命令已提交，设备可能会短暂离线",
    successTitle: "重启指令已发送",
    successText: "重启命令已下发，请等待设备重新上线"
  });
}

function setConfig() {
  const body = {
    report_period_s: Number(document.getElementById("cfg-report-period").value || 5),
    temp_high_limit: Number(document.getElementById("cfg-temp-high").value || 30),
    humidity_high_limit: Number(document.getElementById("cfg-humi-high").value || 80),
    auto_rule_enable: document.getElementById("cfg-auto-rule").checked
  };
  publishAndShow("/api/config", body, {
    pendingTitle: "正在下发配置",
    pendingText: "配置命令已提交，等待设备应用",
    successTitle: "配置已发送",
    successText: "配置下发完成，等待设备应用"
  });
}

async function addManagedDevice() {
  const input = document.getElementById("device-add-input");
  const deviceId = String((input && input.value) || "").trim();

  if (!deviceId) {
    setCommandFeedback("error", "无法添加设备", "请先输入设备 ID");
    return;
  }

  setCommandFeedback("pending", "正在添加设备", `正在将设备 ${deviceId} 加入管理列表`);
  setRawCommand("等待设备管理操作...");

  try {
    const res = await apiPost("/api/devices", { device_id: deviceId });
    const data = res.data || {};
    currentDeviceId = data.selected_device_id || deviceId;
    cachedLiveMessages = [];
    cachedStoredEvents = [];
    if (input) input.value = "";
    applyDevicePayload(data, currentDeviceId);
    setRawCommand(JSON.stringify(data, null, 2));
    setCommandFeedback("success", "设备已添加", `设备 ${deviceId} 已加入列表，并切换为当前设备`);
    await refreshAll();
  } catch (error) {
    setRawCommand(`Error: ${error.message}`);
    setCommandFeedback("error", "添加失败", `添加设备失败：${error.message}`);
  }
}

async function removeManagedDevice(deviceId) {
  const row = cachedDeviceRows.find((item) => item.device_id === deviceId);
  if (!row) {
    setCommandFeedback("error", "无法删除设备", `未找到设备 ${deviceId}`);
    return;
  }

  if (row.removable === false) {
    setCommandFeedback("error", "无法删除设备", `默认设备 ${deviceId} 不能删除`);
    return;
  }

  const isCurrent = deviceId === currentDeviceId;
  if (!window.confirm(`确认删除设备 ${deviceId} 吗？`)) {
    return;
  }

  setCommandFeedback("pending", "正在删除设备", `正在从管理列表中移除设备 ${deviceId}`);
  setRawCommand("等待设备管理操作...");

  try {
    const res = await apiPost("/api/devices/delete", {
      device_id: deviceId,
      selected_device_id: currentDeviceId
    });
    const data = res.data || {};
    currentDeviceId = data.selected_device_id || defaultDeviceId;
    cachedLiveMessages = [];
    cachedStoredEvents = [];
    applyDevicePayload(data, currentDeviceId);
    setRawCommand(JSON.stringify(data, null, 2));
    setCommandFeedback(
      "success",
      "设备已删除",
      isCurrent
        ? `设备 ${deviceId} 已删除，当前视图已自动切换到 ${currentDeviceId}`
        : `设备 ${deviceId} 已从管理列表移除`
    );
    await refreshAll();
  } catch (error) {
    setRawCommand(`Error: ${error.message}`);
    setCommandFeedback("error", "删除失败", `删除设备失败：${error.message}`);
  }
}

async function refreshAll() {
  refreshActiveDeviceLabel();
  updateExportLink();
  await refreshStatus();
  lastHistorySignature = "";
  await Promise.all([refreshHistory({ force: true }), refreshEvents()]);
}

async function selectDevice(nextDeviceId) {
  const deviceId = String(nextDeviceId || "").trim();
  if (!deviceId || deviceId === currentDeviceId) return;

  currentDeviceId = deviceId;
  cachedLiveMessages = [];
  cachedStoredEvents = [];
  refreshActiveDeviceLabel();
  updateExportLink();
  renderDeviceOptions(cachedDeviceRows, currentDeviceId);
  updateDeviceSwitchSummary(cachedDeviceRows, currentDeviceId);
  renderManagedDeviceList();
  await refreshAll();
}

function bindDeviceSwitcher() {
  const select = document.getElementById("device-select");
  const listEl = document.getElementById("device-switch-list");

  if (select) {
    select.addEventListener("change", async (event) => {
      await selectDevice(event.target.value);
    });
  }

  if (listEl) {
    listEl.addEventListener("click", async (event) => {
      const deleteButton = event.target.closest(".device-delete-btn");
      if (deleteButton) {
        const deviceId = String(deleteButton.dataset.deviceId || "").trim();
        if (!deviceId) return;
        await removeManagedDevice(deviceId);
        return;
      }

      const switchButton = event.target.closest(".device-switch-pick");
      if (!switchButton) return;
      await selectDevice(switchButton.dataset.deviceId);
    });
  }
}

function bindDeviceManager() {
  const addInput = document.getElementById("device-add-input");
  const addBtn = document.getElementById("device-add-btn");
  const listEl = document.getElementById("device-manage-list");

  if (addInput) {
    addInput.addEventListener("keydown", async (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      await addManagedDevice();
    });
  }

  if (addBtn) {
    addBtn.addEventListener("click", addManagedDevice);
  }

  if (listEl) {
    listEl.addEventListener("click", async (event) => {
      const button = event.target.closest(".device-delete-btn");
      if (!button) return;
      const deviceId = String(button.dataset.deviceId || "").trim();
      if (!deviceId) return;
      await removeManagedDevice(deviceId);
    });
  }
}

function bindHistoryFilters() {
  const queryBtn = document.getElementById("history-query-btn");
  const resetBtn = document.getElementById("history-reset-btn");
  const inputs = [
    document.getElementById("history-start-at"),
    document.getElementById("history-end-at")
  ].filter(Boolean);

  inputs.forEach((input) => {
    input.addEventListener("keydown", async (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      await submitHistoryQuery();
    });
  });

  if (queryBtn) {
    queryBtn.addEventListener("click", submitHistoryQuery);
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", resetHistoryQuery);
  }
}

window.setSwitch = setSwitch;
window.setTimer = setTimer;
window.cancelTimer = cancelTimer;
window.queryTimer = queryTimer;
window.queryStatus = queryStatus;
window.restartDevice = restartDevice;
window.setConfig = setConfig;

window.addEventListener("load", async () => {
  bindDeviceSwitcher();
  bindDeviceManager();
  bindHistoryFilters();
  await refreshDevices();
  await refreshAll();
  setInterval(refreshStatus, 1000);
  setInterval(refreshHistory, 5000);
  setInterval(refreshEvents, 5000);
});

window.addEventListener("resize", requestChartResize);
window.addEventListener("orientationchange", () => {
  window.setTimeout(requestChartResize, 120);
});

if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", requestChartResize);
}
