"""Strict TLS policy for Draw Things connections.

Certificate chain AND hostname verification are always on. A policy can name an explicit CA file and an explicit TLS
server name (connect by IP while verifying a DNS name in the certificate). There is deliberately no option that
disables hostname verification and no automatic fallback after a TLS failure.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path

from dtflow.core.models import ValidationError

# OpenSSL X509_V_ERR_* codes surfaced as ssl.SSLCertVerificationError.verify_code.
_EXPIRED = 10
_HOSTNAME_CODES = (62, 64)  # hostname / IP address mismatch


@dataclass(frozen=True, slots=True)
class TlsPolicy:
    """Trust source plus optional explicit server name; always verifies chain and hostname."""

    ca_file: Path | None = None
    server_name: str | None = None
    context: ssl.SSLContext | None = None

    def __post_init__(self) -> None:
        if self.server_name is not None and (not isinstance(self.server_name, str) or not self.server_name.strip()):
            raise ValidationError("TLS server name must be a non-empty string.")
        if self.context is not None:
            if self.ca_file is not None:
                raise ValidationError("Use either an injected TLS context or a CA file, not both.")
            if not self.context.check_hostname or self.context.verify_mode != ssl.CERT_REQUIRED:
                raise ValidationError("An injected TLS context must verify certificates and hostnames.")

    @classmethod
    def system(cls, server_name: str | None = None) -> TlsPolicy:
        return cls(None, server_name)

    @classmethod
    def custom_ca(cls, ca_file: str | Path, server_name: str | None = None) -> TlsPolicy:
        return cls(Path(ca_file), server_name)

    def build_context(self) -> ssl.SSLContext:
        if self.context is not None:
            context = self.context
        else:
            if self.ca_file is not None and not Path(self.ca_file).is_file():
                raise ValidationError("TLS CA file does not exist.")
            context = ssl.create_default_context(
                ssl.Purpose.SERVER_AUTH, cafile=str(self.ca_file) if self.ca_file is not None else None
            )
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["h2"])
        return context


def classify_tls_error(exc: BaseException) -> str | None:
    """Return a stable sanitized code for a TLS failure, or None when it is not a TLS error."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        if exc.verify_code == _EXPIRED:
            return "tls_expired"
        if exc.verify_code in _HOSTNAME_CODES:
            return "tls_hostname_mismatch"
        return "tls_untrusted_certificate"
    if isinstance(exc, ssl.SSLError):
        return "tls_handshake_failed"
    return None
