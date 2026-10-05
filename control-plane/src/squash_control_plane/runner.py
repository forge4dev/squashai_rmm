import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
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
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
