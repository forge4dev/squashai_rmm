from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import text

from squash_control_plane.database import Database
from squash_control_plane.models import Device, Execution
from squash_control_plane.schemas import ExecutionStatus
from squash_control_plane.sensitive import configure_sensitive_fields


def test_sensitive_execution_fields_are_encrypted_at_rest() -> None:
    configure_sensitive_fields(Fernet.generate_key().decode("ascii"), allow_plaintext=False)
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_development_schema()
    device_id = uuid4()
    execution_id = uuid4()
    script = "Write-Output 'sensitive value'"

    with database.session_factory.begin() as session:
        session.add(Device(id=device_id, fingerprint="e" * 64, hostname="TEST-PC"))
        session.add(
            Execution(
                id=execution_id,
                device_id=device_id,
                caller_id="caller",
                idempotency_key="encrypted-test",
                request_sha256="a" * 64,
                script=script,
                script_sha256="b" * 64,
                timeout_seconds=30,
                status=ExecutionStatus.SUCCEEDED.value,
                delivery_deadline=datetime.now(UTC) + timedelta(seconds=30),
                completed_at=datetime.now(UTC),
                stdout="secret output",
                stderr="secret error",
            )
        )

    with database.engine.connect() as connection:
        raw = connection.execute(
            text("SELECT script, stdout, stderr FROM executions WHERE id = :id"),
            {"id": execution_id.hex},
        ).one()
    with database.session_factory() as session:
        restored = session.get(Execution, execution_id)

    assert raw.script.startswith("enc:v1:")
    assert raw.stdout.startswith("enc:v1:")
    assert raw.stderr.startswith("enc:v1:")
    assert script not in raw.script
    assert restored is not None
    assert restored.script == script
    assert restored.stdout == "secret output"
    assert restored.stderr == "secret error"
    database.close()
    configure_sensitive_fields("", allow_plaintext=True)
