from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import streamlit as st
from openai import APIError, OpenAI

from .control_plane import ControlPlaneClient, ControlPlaneError
from .driver import DiagnosticDriver, DriverSettings

SUGGESTIONS = {
    ":material/speed: This machine feels slow": "This machine feels slow. Find the likely cause.",
    ":material/lan: Cannot reach the file server": (
        "Why can't this machine reach fileserver.demo.local? Check the network path step by step."
    ),
    ":material/hard_drive: Check disk pressure": (
        "Check whether disk capacity or disk activity is causing a performance problem."
    ),
}


class StreamlitTrace:
    def __init__(self, planning_status: Any) -> None:
        self.events: list[dict[str, Any]] = []
        self._steps: dict[int, Any] = {}
        self._planning_status = planning_status
        self._plan_closed = False

    def __call__(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append({"event": event, "payload": payload.copy()})
        if event == "tool_started":
            self._close_plan("Diagnostic plan ready")
            step_number = int(payload["step"])
            step = st.status(
                f"Step {step_number} · {payload['purpose']}",
                expanded=True,
                type="step",
            )
            step.caption(f"Timeout: {payload['timeout_seconds']} seconds")
            step.code(payload["script"], language="powershell")
            self._steps[step_number] = step
        elif event == "tool_finished":
            self._render_finished(payload)
        elif event == "report_ready":
            self._close_plan("Analysis complete")

    def fail(self, message: str) -> None:
        self._planning_status.update(label="Diagnosis failed", state="error", expanded=True)
        self._planning_status.error(message, icon=":material/error:")

    def _close_plan(self, label: str) -> None:
        if self._plan_closed:
            return
        self._planning_status.update(label=label, state="complete", expanded=False)
        self._plan_closed = True

    def _render_finished(self, payload: dict[str, Any]) -> None:
        step_number = int(payload["step"])
        step = self._steps.get(step_number)
        if step is None:
            return
        if payload.get("error"):
            step.error(payload["error"], icon=":material/error:")
            step.update(label=f"Step {step_number} · Dispatch failed", state="error")
            return

        with step:
            metrics = st.columns(3)
            metrics[0].metric("Status", str(payload.get("status", "unknown")))
            metrics[1].metric("Endpoint", _format_ms(payload.get("duration_ms")))
            metrics[2].metric("Round trip", _format_ms(payload.get("round_trip_ms")))
            _render_output(payload)

        state = "complete" if payload.get("ok") else "error"
        step.update(state=state, expanded=not payload.get("ok", False))


def main() -> None:
    st.set_page_config(
        page_title="Squash AI diagnostics",
        page_icon=":material/terminal:",
        layout="wide",
    )
    st.session_state.setdefault("diagnostic_runs", [])
    st.session_state.setdefault("event_log_result", None)

    settings = _render_sidebar()

    st.title("AI endpoint diagnostics", icon=":material/terminal:")
    st.caption(
        "Select one enrolled endpoint, describe the problem, and watch the AI run sequential "
        "PowerShell diagnostics through the authenticated control plane."
    )

    devices, connection_error = _load_devices(settings)
    selected_device = _render_device_selector(devices, connection_error)
    _render_device_summary(selected_device)

    view = st.segmented_control(
        "Endpoint view",
        ["AI diagnostics", "Inventory", "Event logs"],
        default="AI diagnostics",
        required=True,
        key="endpoint_view",
        width="stretch",
    )
    if view == "Inventory":
        _render_inventory(selected_device, settings)
        return
    if view == "Event logs":
        _render_event_logs(selected_device, settings)
        return

    _render_history()

    ready = bool(selected_device and settings["openai_api_key"])
    if not settings["openai_api_key"]:
        st.caption("Add an OpenAI API key in Connection settings to enable diagnosis.")

    suggested_prompt = None
    if not st.session_state.diagnostic_runs and ready:
        suggestion = st.pills(
            "Try a demo scenario",
            list(SUGGESTIONS),
            selection_mode="single",
            key="demo_suggestion",
        )
        if suggestion:
            suggested_prompt = SUGGESTIONS[suggestion]

    prompt = suggested_prompt or st.chat_input(
        "Describe the endpoint problem",
        key="diagnostic_prompt",
        max_chars=2_000,
        disabled=not ready,
        submit_mode="disable",
    )
    if prompt and selected_device:
        _run_diagnosis(prompt, selected_device, settings)


def _render_sidebar() -> dict[str, Any]:
    with st.sidebar:
        st.header("Connection settings", icon=":material/settings_ethernet:")
        st.caption("Secrets remain in this browser session and are never written by the UI.")
        control_plane_url = st.text_input(
            "Control-plane URL",
            value=os.getenv("SQUASH_CONTROL_PLANE_URL", "https://rmm.demo.local:8443"),
            key="control_plane_url",
        )
        control_plane_api_key = st.text_input(
            "Control-plane API key",
            value=os.getenv("SQUASH_API_KEY", ""),
            type="password",
            key="control_plane_api_key",
        )
        openai_api_key = st.text_input(
            "OpenAI API key",
            value=os.getenv("OPENAI_API_KEY", ""),
            type="password",
            key="openai_api_key",
        )
        ca_file = st.text_input(
            "Control-plane CA certificate",
            value=os.getenv(
                "SQUASH_CA_FILE",
                r"C:\ProgramData\SquashRMM\ControlPlane\server-cert.pem",
            ),
            key="ca_file",
            help="PEM certificate used to verify the demo control plane's HTTPS identity.",
        )
        model = st.text_input(
            "OpenAI model",
            value=os.getenv("OPENAI_MODEL", "gpt-6-astra"),
            key="openai_model",
        )

        with st.expander("Diagnostic limits", icon=":material/tune:"):
            max_steps = st.slider("Maximum steps", 1, 12, 8, key="max_steps")
            delivery_timeout = st.slider(
                "Offline deadline (seconds)", 1, 60, 15, key="delivery_timeout"
            )

        with st.container(horizontal=True):
            st.button("Refresh devices", icon=":material/refresh:", key="refresh_devices")
            if st.button("Clear transcript", icon=":material/delete:", key="clear_transcript"):
                st.session_state.diagnostic_runs = []
                st.rerun()

        st.caption("Squash AI driver · assessment build")

    return {
        "control_plane_url": control_plane_url,
        "control_plane_api_key": control_plane_api_key,
        "openai_api_key": openai_api_key,
        "ca_file": ca_file.strip() or None,
        "model": model,
        "max_steps": max_steps,
        "delivery_timeout": delivery_timeout,
    }


def _load_devices(settings: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    if not settings["control_plane_api_key"]:
        return [], "Add the control-plane API key in Connection settings."
    try:
        with _control_plane(settings) as client:
            return client.list_devices(), None
    except (ControlPlaneError, OSError, ValueError) as exc:
        return [], str(exc)


def _render_device_selector(
    devices: list[dict[str, Any]], connection_error: str | None
) -> dict[str, Any] | None:
    if connection_error:
        st.error(connection_error, icon=":material/cloud_off:")
        return None
    active_devices = [device for device in devices if not device.get("revoked", False)]
    if not active_devices:
        st.warning("No enrolled, non-revoked devices were found.", icon=":material/computer:")
        return None

    labels = {
        _device_label(device): device
        for device in sorted(active_devices, key=lambda item: str(item["hostname"]).casefold())
    }
    selected_label = st.selectbox(
        "Target endpoint",
        list(labels),
        key="selected_device",
        help="The model cannot change this device during a diagnostic run.",
    )
    return labels[selected_label]


def _render_device_summary(device: dict[str, Any] | None) -> None:
    if device is None:
        return
    with st.container(border=True):
        columns = st.columns(3)
        columns[0].metric("Endpoint", device["hostname"])
        columns[1].metric("Reachability", "Online" if device["online"] else "Offline")
        columns[2].metric("Agent", device.get("agent_version") or "Unknown")
        st.caption(
            f"Device ID: `{device['id']}` · Last seen: {device.get('last_seen_at') or 'never'}"
        )


def _render_inventory(device: dict[str, Any] | None, settings: dict[str, Any]) -> None:
    st.header("Device inventory", icon=":material/inventory_2:")
    if device is None:
        st.caption("Connect to the control plane and select an endpoint to view inventory.")
        return
    try:
        with _control_plane(settings) as client:
            inventory = client.get_inventory(str(device["id"]))
    except ControlPlaneError as exc:
        if "HTTP 404" in str(exc):
            st.warning(
                "This agent has not uploaded inventory yet. Restart or upgrade the agent and refresh.",
                icon=":material/pending:",
            )
        else:
            st.error(str(exc), icon=":material/error:")
        return

    operating_system = inventory.get("os") or {}
    hardware = inventory.get("hardware") or {}
    with st.container(horizontal=True):
        st.metric(
            "Operating system",
            operating_system.get("caption") or "Windows",
            border=True,
        )
        st.metric("Build", operating_system.get("build") or "Unknown", border=True)
        st.metric("Memory", _format_bytes(hardware.get("memory_bytes")), border=True)
        st.metric(
            "Installed software",
            str(len(inventory.get("installed_software") or [])),
            border=True,
        )
    st.caption(
        f"Collected: {inventory.get('collected_at')} · "
        f"Received: {inventory.get('received_at')} · "
        f"Last boot: {inventory.get('last_boot_at') or 'unknown'}"
    )

    hardware_summary = {
        "Manufacturer": hardware.get("manufacturer") or "Unknown",
        "Model": hardware.get("model") or "Unknown",
        "Serial number": hardware.get("serial_number") or "Unknown",
        "Processor": hardware.get("processor_name") or "Unknown",
        "Logical processors": hardware.get("processor_count") or "Unknown",
        "BIOS": hardware.get("bios_version") or "Unknown",
        "Architecture": operating_system.get("architecture") or "Unknown",
    }
    st.subheader("Hardware", icon=":material/memory:")
    st.table(
        hardware_summary,
        border="horizontal",
        width="stretch",
        alt="Hardware details for the selected endpoint",
    )

    disk_rows = []
    for disk in inventory.get("disks") or []:
        total = int(disk.get("total_bytes") or 0)
        free = int(disk.get("free_bytes") or 0)
        disk_rows.append(
            {
                "Drive": disk.get("name"),
                "Format": disk.get("format"),
                "Capacity": _format_bytes(total),
                "Free": _format_bytes(free),
                "Free percent": round((free / total) * 100, 1) if total else None,
            }
        )
    st.subheader("Storage", icon=":material/hard_drive:")
    st.dataframe(disk_rows, hide_index=True, alt="Current endpoint disk capacity and free space")

    st.subheader("Installed software", icon=":material/apps:")
    software = inventory.get("installed_software") or []
    st.dataframe(
        software,
        hide_index=True,
        alt="Software currently installed on the selected endpoint",
    )


def _render_event_logs(device: dict[str, Any] | None, settings: dict[str, Any]) -> None:
    st.header("Windows event logs", icon=":material/event_note:")
    st.caption("Query bounded, structured events without dispatching a hand-written script.")
    if device is None:
        return

    with st.form("event_log_query", border=True):
        logs = st.multiselect(
            "Logs",
            ["System", "Application", "Setup"],
            default=["System", "Application"],
        )
        severities = st.pills(
            "Severity",
            ["critical", "error", "warning", "information"],
            default=["critical", "error", "warning"],
            selection_mode="multi",
            wrap=True,
        )
        columns = st.columns(2)
        lookback_hours = columns[0].number_input(
            "Lookback hours", min_value=1, max_value=168, value=24
        )
        max_events = columns[1].number_input(
            "Maximum events", min_value=1, max_value=200, value=100
        )
        provider_text = st.text_input(
            "Providers (optional)",
            placeholder="Service Control Manager, Microsoft-Windows-Kernel-Power",
        )
        event_id_text = st.text_input("Event IDs (optional)", placeholder="41, 6008, 7031")
        submitted = st.form_submit_button(
            "Query event logs", icon=":material/search:", type="primary"
        )

    if submitted:
        try:
            event_ids = _parse_event_ids(event_id_text)
            if not logs or not severities:
                raise ValueError("Select at least one log and one severity")
            providers = [value.strip() for value in provider_text.split(",") if value.strip()]
            end_time = datetime.now(UTC)
            filters = {
                "logs": logs,
                "severities": severities,
                "provider_names": providers,
                "event_ids": event_ids,
                "start_time": (end_time - timedelta(hours=int(lookback_hours))).isoformat(),
                "end_time": end_time.isoformat(),
                "max_events": int(max_events),
                "timeout_seconds": 30,
                "delivery_timeout_seconds": int(settings["delivery_timeout"]),
            }
            with st.status("Querying the endpoint", expanded=True) as query_status:
                with _control_plane(settings) as client:
                    result, round_trip_ms = client.query_event_logs(str(device["id"]), filters)
                if result.get("status") == "succeeded" and not result.get("parse_error"):
                    query_status.update(label="Event query complete", state="complete")
                else:
                    query_status.update(label="Event query did not succeed", state="error")
                st.caption(
                    f"Execution: {_format_ms(result.get('duration_ms'))} · "
                    f"Round trip: {_format_ms(round_trip_ms)}"
                )
            st.session_state.event_log_result = {
                "device_id": str(device["id"]),
                "result": result,
                "round_trip_ms": round_trip_ms,
            }
        except (ControlPlaneError, OSError, ValueError) as exc:
            st.error(str(exc), icon=":material/error:")

    saved = st.session_state.event_log_result
    if saved and saved.get("device_id") == str(device["id"]):
        _render_event_log_result(saved["result"], saved["round_trip_ms"])


def _render_event_log_result(result: dict[str, Any], round_trip_ms: float) -> None:
    events = result.get("events") or []
    with st.container(horizontal=True):
        st.metric("Status", result.get("status", "unknown"), border=True)
        st.metric("Events", str(len(events)), border=True)
        st.metric("Endpoint", _format_ms(result.get("duration_ms")), border=True)
        st.metric("Round trip", _format_ms(round_trip_ms), border=True)
    if result.get("terminal_reason"):
        st.error(result["terminal_reason"], icon=":material/error:")
    if result.get("parse_error"):
        st.error(result["parse_error"], icon=":material/data_alert:")
    if not events:
        st.caption("No matching events were returned.")
        return
    st.dataframe(
        events,
        hide_index=True,
        column_config={
            "time_created": st.column_config.DatetimeColumn("Time", format="localized"),
            "message": st.column_config.TextColumn("Message", width="large"),
        },
        alt="Structured Windows events returned by the selected endpoint",
    )


def _render_history() -> None:
    for run_index, run in enumerate(st.session_state.diagnostic_runs):
        with st.chat_message("user"):
            st.markdown(run["problem"])
        with st.chat_message("assistant", avatar=":material/robot_2:"):
            _render_static_trace(run["events"])
            st.markdown(run["report"])
            st.download_button(
                "Download report",
                run["report"],
                file_name=f"squash-diagnostic-{run_index + 1}.md",
                mime="text/markdown",
                icon=":material/download:",
                key=f"download_report_{run_index}",
            )


def _run_diagnosis(problem: str, device: dict[str, Any], settings: dict[str, Any]) -> None:
    with st.chat_message("user"):
        st.markdown(problem)

    with st.chat_message("assistant", avatar=":material/robot_2:"):
        planning = st.status(":shimmer[Planning diagnostics]", expanded=True, type="compact")
        trace = StreamlitTrace(planning)
        try:
            with _control_plane(settings) as control_plane:
                driver = DiagnosticDriver(
                    OpenAI(api_key=settings["openai_api_key"]).responses,
                    control_plane,
                    device,
                    DriverSettings(
                        model=settings["model"],
                        max_steps=int(settings["max_steps"]),
                        delivery_timeout_seconds=int(settings["delivery_timeout"]),
                    ),
                    event_handler=trace,
                )
                report = driver.diagnose(problem)
        except (APIError, ControlPlaneError, OSError, ValueError) as exc:
            trace.fail(str(exc))
            st.error(f"Diagnosis stopped: {exc}", icon=":material/error:")
            return

        st.markdown(report)
        st.download_button(
            "Download report",
            report,
            file_name="squash-diagnostic.md",
            mime="text/markdown",
            icon=":material/download:",
            key=f"download_current_{len(st.session_state.diagnostic_runs)}",
        )
        st.session_state.diagnostic_runs.append(
            {
                "problem": problem,
                "report": report,
                "events": trace.events,
                "device_id": str(device["id"]),
            }
        )
        # The live response is emitted after st.chat_input during this run.
        # Rebuild from persisted history so the main-body chat input is once
        # again the final element and Streamlit pins it to the page bottom.
        st.rerun()


def _render_static_trace(events: list[dict[str, Any]]) -> None:
    started = {
        int(item["payload"]["step"]): item["payload"]
        for item in events
        if item["event"] == "tool_started"
    }
    finished = {
        int(item["payload"]["step"]): item["payload"]
        for item in events
        if item["event"] == "tool_finished"
    }
    for step_number, start in started.items():
        result = finished.get(step_number, {})
        state = "complete" if result.get("ok") else "error"
        with st.status(
            f"Step {step_number} · {start['purpose']}",
            state=state,
            expanded=False,
            type="step",
        ):
            st.code(start["script"], language="powershell")
            if result.get("error"):
                st.error(result["error"], icon=":material/error:")
            elif result:
                metrics = st.columns(3)
                metrics[0].metric("Status", str(result.get("status", "unknown")))
                metrics[1].metric("Endpoint", _format_ms(result.get("duration_ms")))
                metrics[2].metric("Round trip", _format_ms(result.get("round_trip_ms")))
                _render_output(result)


def _render_output(result: dict[str, Any]) -> None:
    stdout = str(result.get("stdout", ""))
    stderr = str(result.get("stderr", ""))
    if stdout:
        with st.expander(
            "Standard output" + (" · truncated" if result.get("stdout_truncated") else ""),
            icon=":material/terminal:",
        ):
            try:
                st.json(json.loads(stdout), expanded=1)
            except json.JSONDecodeError:
                st.code(stdout, language="text", wrap_lines=True)
    if stderr:
        with st.expander(
            "Standard error" + (" · truncated" if result.get("stderr_truncated") else ""),
            icon=":material/warning:",
        ):
            st.code(stderr, language="text", wrap_lines=True)


def _control_plane(settings: dict[str, Any]) -> ControlPlaneClient:
    return ControlPlaneClient(
        settings["control_plane_url"],
        settings["control_plane_api_key"],
        ca_file=settings["ca_file"],
    )


def _device_label(device: dict[str, Any]) -> str:
    state = "Online" if device.get("online") else "Offline"
    return f"{device['hostname']} · {state} · {device['id']}"


def _format_ms(value: Any) -> str:
    if value is None:
        return "—"
    return f"{int(value):,} ms"


def _format_bytes(value: Any) -> str:
    if value is None:
        return "Unknown"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:,.1f} {unit}"
        size /= 1024
    return "Unknown"


def _parse_event_ids(value: str) -> list[int]:
    if not value.strip():
        return []
    event_ids = []
    for item in value.split(","):
        try:
            event_id = int(item.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid event ID: {item.strip()!r}") from exc
        if not 0 <= event_id <= 65535:
            raise ValueError("Event IDs must be between 0 and 65535")
        event_ids.append(event_id)
    if len(set(event_ids)) > 50:
        raise ValueError("At most 50 event IDs may be queried")
    return list(dict.fromkeys(event_ids))


if __name__ == "__main__":
    main()
