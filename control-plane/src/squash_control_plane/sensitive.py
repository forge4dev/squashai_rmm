from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import Text, TypeDecorator

_fernet: Fernet | None = None
_allow_plaintext = False


def configure_sensitive_fields(key: str, *, allow_plaintext: bool) -> None:
    global _allow_plaintext, _fernet
    _allow_plaintext = allow_plaintext
    _fernet = Fernet(key.encode("ascii")) if key else None
    if _fernet is None and not allow_plaintext:
        raise RuntimeError("Sensitive-field encryption is not configured")


class SensitiveText(TypeDecorator[str]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        if _fernet is None:
            if _allow_plaintext:
                return value
            raise RuntimeError("Sensitive-field encryption is not configured")
        token = _fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return f"enc:v1:{token}"

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None or not value.startswith("enc:v1:"):
            if _allow_plaintext:
                return value
            raise RuntimeError("Encountered an unencrypted sensitive database field")
        if _fernet is None:
            raise RuntimeError("Sensitive-field encryption is not configured")
        try:
            return _fernet.decrypt(value[7:].encode("ascii")).decode("utf-8")
        except InvalidToken as error:
            raise RuntimeError("Sensitive database field could not be decrypted") from error
