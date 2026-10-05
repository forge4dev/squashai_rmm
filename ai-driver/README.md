# Squash minimal AI diagnostic driver

This CLI takes a plain-English problem, lets an OpenAI model choose sequential
read-only PowerShell diagnostics, and executes those diagnostics only through the
authenticated Squash control-plane API. The target device is selected before the
model runs, so the model cannot redirect execution to another endpoint.

## Install

Use Python 3.12 from the repository root:

```powershell
cd ai-driver
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "."
```

## Configure

Set secrets in the current PowerShell process. Do not put them in source files,
command arguments, screenshots, or the demo recording.

```powershell
$env:OPENAI_API_KEY = Read-Host "OpenAI API key"
$env:SQUASH_API_KEY = Read-Host "Control-plane API key"
$env:SQUASH_CONTROL_PLANE_URL = "https://rmm.demo.local:8443"
```

Python does not always use the Windows trusted-root store. If the control plane
uses the demo CA, export that CA/root certificate as a PEM file and set:

```powershell
$env:SQUASH_CA_FILE = "C:\ProgramData\SquashRMM\ControlPlane\server-cert.pem"
```

Do not disable TLS verification. `--allow-http` exists only for the local HTTP
development server.

## Run

### Demo UI

Start the browser interface from `ai-driver`:

```powershell
.\.venv\Scripts\streamlit.exe run streamlit_app.py
```

Then open <http://127.0.0.1:8501>. The server is intentionally bound to
localhost because this assessment UI does not implement user authentication.
Enter the two API keys in the password fields, select an enrolled endpoint, and
submit a problem. Environment variables from the Configure section prefill the
same fields, which is useful when recording the demo.

Use the **Inventory** view to show the latest OS, hardware, storage, software,
and last-boot snapshot. Use **Event logs** to select logs and severity, constrain
the lookback window, and display structured Windows events with endpoint and
round-trip timing. Neither view needs an OpenAI API key; they authenticate only
to the control plane.

The interface shows:

- current online/offline state and agent version;
- each exact PowerShell script in a live diagnostic timeline;
- structured stdout, stderr, exit status, and truncation state;
- endpoint execution time and full round-trip time per step;
- device inventory and filtered, structured event-log views;
- the final AI report and a report download button.

For the video, use an online endpoint first and ask the connectivity example
below. Then stop the Windows agent, click **Refresh devices**, lower **Offline
deadline** to five seconds, and run a disk-space question to show the terminal
`delivery_expired` result.

### Command-line interface

Select the endpoint by exact hostname or device UUID:

```powershell
.\.venv\Scripts\squash-diagnose.exe `
  --device "WINDOWS-VM" `
  "Why does this machine feel slow?"
```

For the connectivity demo:

```powershell
.\.venv\Scripts\squash-diagnose.exe `
  --device "WINDOWS-VM" `
  "Why can't this machine reach fileserver.demo.local?"
```

The CLI prints each purpose, exact script, structured result, endpoint execution
duration, and total round-trip time. This produces the terminal transcript needed
for the assessment video. If there is exactly one non-revoked device, `--device`
may be omitted.

To demonstrate offline handling, stop the Windows agent first and use a short
delivery deadline:

```powershell
.\.venv\Scripts\squash-diagnose.exe `
  --device "WINDOWS-VM" `
  --delivery-timeout 5 `
  "Check whether the machine has enough free disk space."
```

## Safety boundary

The model receives one strict tool: run bounded PowerShell on the already-selected
device. The harness caps steps, execution time, script size, and model-visible
output; prevents parallel calls; checks the dispatched and executed SHA-256 hashes;
and marks endpoint output as untrusted. The prompt limits this assessment driver to
read-only diagnosis. This is defense in depth, not a PowerShell sandbox. In a
production system, enforce an approval/policy layer outside this driver before
allowing remediation.

The OpenAI request uses `store=False` and manually carries response items, including
reasoning items, across tool calls. Endpoint evidence is still sent to the configured
OpenAI API, so diagnostic scripts must collect only the minimum necessary data and
must not retrieve secrets.

