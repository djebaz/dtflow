"""Download tokens (Civitai) in Windows Credential Manager, separate from the DT+ API keys.

Each provider has exactly one vault entry under the ``DTFlow`` service. There is no index file and no name: app data
never holds the token or a reference to it, and a plaintext fallback is never offered.
"""

from dtflow.core.models import ValidationError
from dtflow.services.credentials import SERVICE, SecureStorageUnavailable, secure_vault

CIVITAI = "civitai"
ACCOUNTS = {CIVITAI: "civitai-token"}
MAX_TOKEN = 512


def _clean(secret: str) -> str:
    value = secret.strip()
    if not value:
        raise ValidationError("A token cannot be empty.")
    if len(value) > MAX_TOKEN or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValidationError("That does not look like a token: it has spaces or is too long.")
    return value


class DownloadTokens:
    """One saved token per download provider; reads and writes are blocking and belong on a worker thread."""

    def __init__(self, vault=None):
        self._vault = vault

    def _store(self):
        return self._vault if self._vault is not None else secure_vault()

    @staticmethod
    def _account(provider: str) -> str:
        try:
            return ACCOUNTS[provider]
        except KeyError:
            raise ValidationError("Unknown download provider.") from None

    def available(self) -> bool:
        try:
            self._store()
        except SecureStorageUnavailable:
            return False
        return True

    def get(self, provider: str = CIVITAI) -> str | None:
        """The saved token, or ``None`` when none is saved; raises ``SecureStorageUnavailable`` without a vault."""
        value = self._store().get_password(SERVICE, self._account(provider))
        value = value.strip() if value is not None else ""
        return value or None

    def present(self, provider: str = CIVITAI) -> bool:
        return self.get(provider) is not None

    def save(self, provider: str, secret: str) -> None:
        value = _clean(secret)
        store = self._store()
        store.set_password(SERVICE, self._account(provider), value)

    def remove(self, provider: str = CIVITAI) -> None:
        """Remove the saved token; nothing saved is not an error."""
        store = self._store()
        account = self._account(provider)
        if store.get_password(SERVICE, account) is not None:
            store.delete_password(SERVICE, account)


def token_status(tokens: DownloadTokens, provider: str = CIVITAI) -> str:
    """``present``, ``absent`` or ``unavailable``; never returns the secret."""
    try:
        return "present" if tokens.present(provider) else "absent"
    except SecureStorageUnavailable:
        return "unavailable"
