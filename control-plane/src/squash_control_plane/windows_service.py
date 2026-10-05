import os
from pathlib import Path

import servicemanager
import uvicorn
import win32event
import win32service
import win32serviceutil


class SquashControlPlaneService(win32serviceutil.ServiceFramework):
    _svc_name_ = "SquashRMMControlPlane"
    _svc_display_name_ = "Squash RMM Control Plane"
    _svc_description_ = "Authenticated control plane for Squash Windows endpoint agents."

    def __init__(self, args) -> None:
        super().__init__(args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._server: uvicorn.Server | None = None

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self._server is not None:
            self._server.should_exit = True
        win32event.SetEvent(self._stop_event)

    def SvcDoRun(self) -> None:
        program_data = Path(os.environ["PROGRAMDATA"]) / "SquashRMM" / "ControlPlane"
        os.chdir(program_data)
        from .config import get_settings

        settings = get_settings()
        servicemanager.LogInfoMsg("Squash RMM control plane starting")
        config = uvicorn.Config(
            "squash_control_plane.main:app",
            host=settings.listen_host,
            port=settings.listen_port,
            log_level="info",
            access_log=False,
            ssl_certfile=settings.tls_certificate_file or None,
            ssl_keyfile=settings.tls_private_key_file or None,
            server_header=False,
            ws_max_size=(settings.output_limit_bytes_per_stream * 2) + (256 * 1024),
        )
        self._server = uvicorn.Server(config)
        self._server.run()
        servicemanager.LogInfoMsg("Squash RMM control plane stopped")


def main() -> None:
    win32serviceutil.HandleCommandLine(SquashControlPlaneService)


if __name__ == "__main__":
    main()
