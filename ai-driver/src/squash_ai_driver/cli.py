from __future__ import annotations

import argparse
import os
import sys

from openai import APIError, OpenAI

from .control_plane import ControlPlaneClient, ControlPlaneError
from .driver import DiagnosticDriver, DriverSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="squash-diagnose",
        description="Run a bounded AI-guided diagnosis through the Squash RMM control plane.",
    )
    parser.add_argument("problem", help="Plain-English endpoint problem to investigate")
    parser.add_argument(
        "--control-plane-url",
        default=os.getenv("SQUASH_CONTROL_PLANE_URL", "https://rmm.demo.local:8443"),
    )
    parser.add_argument("--device", help="Device UUID or exact hostname")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-6-astra"))
    parser.add_argument("--ca-file", default=os.getenv("SQUASH_CA_FILE"))
    parser.add_argument("--max-steps", type=_integer_between(1, 12), default=8, metavar="1..12")
    parser.add_argument(
        "--delivery-timeout",
        type=_integer_between(1, 300),
        default=15,
        metavar="SECONDS",
    )
    parser.add_argument(
        "--allow-http",
        action="store_true",
        help="Allow an HTTP control-plane URL for local development only",
    )
    return parser


def _integer_between(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return parse


def main() -> None:
    args = _parser().parse_args()
    openai_key = os.getenv("OPENAI_API_KEY")
    control_plane_key = os.getenv("SQUASH_API_KEY")
    if not openai_key:
        raise SystemExit("OPENAI_API_KEY is not set")
    if not control_plane_key:
        raise SystemExit("SQUASH_API_KEY is not set")

    try:
        with ControlPlaneClient(
            args.control_plane_url,
            control_plane_key,
            ca_file=args.ca_file,
            allow_http=args.allow_http,
        ) as control_plane:
            device = control_plane.select_device(args.device)
            reachability = "online" if device["online"] else "offline"
            print(f"Target: {device['hostname']} ({device['id']}) - {reachability}")
            driver = DiagnosticDriver(
                OpenAI(api_key=openai_key).responses,
                control_plane,
                device,
                DriverSettings(
                    model=args.model,
                    max_steps=args.max_steps,
                    delivery_timeout_seconds=args.delivery_timeout,
                ),
            )
            report = driver.diagnose(args.problem)
    except (APIError, ControlPlaneError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("\nDiagnostic report")
    print("=================")
    print(report)


if __name__ == "__main__":
    main()
