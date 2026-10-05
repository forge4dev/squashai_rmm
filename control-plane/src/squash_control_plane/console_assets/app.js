let apiKey = "";
let selectedDevice = "";
const terminal = new Set(["succeeded", "failed", "timed_out", "delivery_expired", "agent_interrupted"]);
const byId = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function api(path, options = {}) {
  const headers = {...(options.headers || {}), "X-API-Key": apiKey};
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  if (!response.ok) throw new Error(`${response.status}: ${(await response.json()).detail || response.statusText}`);
  return response.json();
}

async function loadDevices() {
  apiKey = byId("api-key").value;
  const devices = await api("/v1/devices");
  const select = byId("device");
  select.replaceChildren();
  for (const device of devices) {
    const option = document.createElement("option");
    option.value = device.id;
    option.textContent = `${device.hostname} — ${device.online ? "online" : "offline"}`;
    select.append(option);
  }
  selectedDevice = select.value;
  byId("connection-state").textContent = `${devices.length} device(s)`;
  await loadHistory();
}

async function loadHistory() {
  if (!selectedDevice) return;
  const rows = await api(`/v1/executions?device_id=${encodeURIComponent(selectedDevice)}&limit=10`);
  const history = byId("history");
  history.replaceChildren();
  for (const row of rows) {
    const item = document.createElement("button");
    item.className = "history-item";
    item.textContent = `${row.status} · ${row.id} · ${new Date(row.created_at).toLocaleTimeString()}`;
    item.onclick = () => render(row, null);
    history.append(item);
  }
}

function render(execution, elapsed) {
  byId("status").textContent = execution.status;
  byId("execution-id").textContent = execution.id;
  if (elapsed !== null) byId("round-trip").textContent = `${elapsed} ms`;
  const result = execution.result;
  byId("duration").textContent = result?.duration_ms == null ? "—" : `${result.duration_ms} ms`;
  byId("exit-code").textContent = result?.exit_code ?? "—";
  byId("hash").textContent = result?.executed_sha256 ?
    (result.executed_sha256 === execution.script_sha256 ? "matched" : "MISMATCH") : "—";
  byId("stdout").textContent = result?.stdout || "";
  byId("stderr").textContent = result?.stderr || "";
}

async function run() {
  selectedDevice = byId("device").value;
  if (!selectedDevice) throw new Error("Select a device");
  const started = performance.now();
  const accepted = await api(`/v1/devices/${selectedDevice}/executions`, {
    method: "POST",
    headers: {"Idempotency-Key": crypto.randomUUID()},
    body: JSON.stringify({
      script: byId("script").value,
      timeout_seconds: Number(byId("timeout").value),
      delivery_timeout_seconds: Number(byId("delivery-timeout").value)
    })
  });
  let execution;
  do {
    execution = await api(`/v1/executions/${accepted.execution_id}`);
    render(execution, Math.round(performance.now() - started));
    if (!terminal.has(execution.status)) await sleep(200);
  } while (!terminal.has(execution.status));
  await loadHistory();
}

function report(error) {
  byId("status").textContent = "Error";
  byId("stderr").textContent = error.message;
}

byId("connect").onclick = () => loadDevices().catch(report);
byId("run").onclick = () => run().catch(report);
byId("device").onchange = (event) => { selectedDevice = event.target.value; loadHistory().catch(report); };
