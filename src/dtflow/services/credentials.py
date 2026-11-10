"""Deterministic environment-first credential resolution plus the secure saved-key store."""

import asyncio
import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dtflow.core.models import (
    BackendError,
    BackendFailure,
    Credential,
    CredentialProvider,
    ErrorCategory,
    ValidationError,
)
from dtflow.services.artifacts import atomic_write
from dtflow.services.cloud_usage import key_identity

KEY = "DRAWTHINGS_API_KEY"


class EnvironmentCredentials:
    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = os.environ if environ is None else environ

    async def get(self) -> Credential | None:
        if KEY not in self.environ:
            return None
        value = self.environ[KEY].strip()
        if not value:
            raise BackendError(
                BackendFailure(
                    ErrorCategory.CREDENTIALS, "empty_environment_key", "Selected environment credential is empty."
                )
            )
        return Credential("environment", value)


class FakeSavedCredentials:
    """Injected test provider only; not Windows Credential Manager storage."""

    def __init__(self, value: str | None = None):
        self._value = value
        self.reads = 0

    async def get(self) -> Credential | None:
        self.reads += 1
        if self._value is None:
            return None
        value = self._value.strip()
        if not value:
            raise BackendError(
                BackendFailure(ErrorCategory.CREDENTIALS, "empty_saved_key", "Selected saved credential is empty.")
            )
        return Credential("saved", value)


ENVIRONMENT = "environment"  # the selectable id of the read-only DRAWTHINGS_API_KEY entry


async def resolve_credentials(environment: CredentialProvider, saved: CredentialProvider) -> Credential:
    """Select one source; never switch after selected validation/auth failure.

    The key chosen in the app (saved) wins over the process environment, unless the person explicitly selected the
    environment key. The environment is the fallback when nothing is saved.
    """
    prefer_environment = bool(getattr(saved, "environment_preferred", lambda: False)())
    first, second = (environment, saved) if prefer_environment else (saved, environment)
    credential = await first.get()
    if credential is None:
        credential = await second.get()
    if credential is None:
        raise BackendError(
            BackendFailure(ErrorCategory.CREDENTIALS, "missing_credentials", "Cloud credentials are missing.")
        )
    return credential


SERVICE = "DTFlow"
ACCOUNT = "drawthings-api-key"


class SecureStorageUnavailable(BackendError):
    """No secure operating-system store exists; a plaintext fallback is never offered."""

    def __init__(self):
        super().__init__(
            BackendFailure(
                ErrorCategory.CREDENTIALS,
                "secure_storage_unavailable",
                "Secure credential storage (Windows Credential Manager) is unavailable.",
            )
        )


def _windows_vault():
    """Return the keyring module only when its active backend is Windows Credential Manager."""
    try:
        import keyring
        from keyring.backends.Windows import WinVaultKeyring
    except Exception:  # missing module or non-Windows import failure
        raise SecureStorageUnavailable() from None
    if not isinstance(keyring.get_keyring(), WinVaultKeyring):
        raise SecureStorageUnavailable()
    return keyring


MAIN = "main"  # id of the original single slot, whose vault account keeps its historical name
MAX_NAME = 40
MAX_INDEX_BYTES = 65536
INDEX_FILE = "cloud_keys.json"
_KEY_LIKE = re.compile(r"dk_[A-Za-z0-9]{6,}")


@dataclass(frozen=True, slots=True)
class SavedKey:
    id: str
    name: str
    credential_id: str
    hint: str | None
    active: bool
    alias: str | None = None  # Only an explicitly saved name, never the generated label.


def key_label(hint: str | None, credential_id: str) -> str:
    """Harmless default name: the four hint characters when known, else four characters of the one-way digest."""
    return f"Key {hint}\u2022\u2022\u2022\u2022" if hint else f"Key {credential_id[:4].upper()}"


def _account(key_id: str) -> str:
    return ACCOUNT if key_id == MAIN else f"{ACCOUNT}:{key_id}"


def _clean_name(name: str | None) -> str | None:
    value = " ".join((name or "").split())
    if not value:
        return None
    if len(value) > MAX_NAME or _KEY_LIKE.search(value) or any(ord(c) < 32 for c in value):
        raise ValidationError(f"A key name is at most {MAX_NAME} characters and never contains a key.")
    return value


def _safe_alias(name: str | None, secret: str) -> str | None:
    """Discard saved names containing their own credential before display or diagnostics."""
    try:
        alias = _clean_name(name)
    except ValidationError:
        return None
    return alias if alias and secret not in alias else None


class SavedCredentials:
    """Keys saved in Windows Credential Manager, with a credential-free index (names and the active choice).

    Secrets live only in the vault, one account per key. The index holds ids, optional names and which key is
    active (a saved id, or ``ENVIRONMENT``); it never holds a secret. Without an index path the index is in memory.
    """

    def __init__(self, vault=None, index: Path | str | None = None):
        self._vault = vault
        self._index = Path(index) if index is not None else None
        self._memory: dict = {"active": None, "keys": []}

    def attach(self, index: Path | str) -> None:
        """Persist the index at ``index`` unless a location was already chosen."""
        if self._index is None:
            self._index = Path(index)

    def _store(self):
        return self._vault if self._vault is not None else _windows_vault()

    def available(self) -> bool:
        try:
            self._store()
        except SecureStorageUnavailable:
            return False
        return True

    # -- credential-free index -------------------------------------------------------------------------------
    def _read(self) -> dict:
        if self._index is None:
            return self._memory
        try:
            raw = self._index.read_bytes()[: MAX_INDEX_BYTES + 1]
            document = json.loads(raw) if len(raw) <= MAX_INDEX_BYTES else {}
        except (OSError, ValueError):
            document = {}
        keys = []
        for item in document.get("keys", []) if isinstance(document, dict) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                name = item.get("name")
                try:
                    name = _clean_name(name if isinstance(name, str) else None)
                except ValidationError:
                    name = None
                keys.append({"id": item["id"], "name": name})
        active = document.get("active") if isinstance(document, dict) else None
        return {"active": active if isinstance(active, str) else None, "keys": keys}

    def _write(self, document: dict) -> None:
        if self._index is None:
            self._memory = document
            return
        payload = {"version": 1, "active": document["active"], "keys": document["keys"]}
        self._index.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self._index, json.dumps(payload, indent=2).encode("utf-8"))

    def _ids(self, store, document: dict) -> list[dict]:
        """Indexed keys that still exist in the vault, plus the historical slot when it holds a key."""
        keys = [k for k in document["keys"] if store.get_password(SERVICE, _account(k["id"])) is not None]
        if all(k["id"] != MAIN for k in keys) and store.get_password(SERVICE, ACCOUNT) is not None:
            keys.insert(0, {"id": MAIN, "name": None})
        return keys

    def _secret(self, store, key_id: str) -> str | None:
        value = store.get_password(SERVICE, _account(key_id))
        return value.strip() if value is not None else None

    # -- reading -----------------------------------------------------------------------------------------------
    def environment_preferred(self) -> bool:
        return self._read()["active"] == ENVIRONMENT

    def active_id(self) -> str | None:
        """The saved key in use: the chosen one, else the first saved key; ``None`` when nothing is saved."""
        store = self._store()
        document = self._read()
        ids = [k["id"] for k in self._ids(store, document)]
        if document["active"] in ids:
            return document["active"]
        return ids[0] if ids else None

    def entries(self) -> list[SavedKey]:
        store = self._store()
        document = self._read()
        keys = self._ids(store, document)
        ids = [k["id"] for k in keys]
        active = document["active"] if document["active"] in ids else (ids[0] if ids else None)
        result: list[SavedKey] = []
        used: dict[str, int] = {}
        for item in keys:
            secret = self._secret(store, item["id"])
            if not secret:
                continue
            credential_id, hint = key_identity(secret)
            alias = _safe_alias(item["name"], secret)
            name = alias or key_label(hint, credential_id)
            used[name] = used.get(name, 0) + 1
            if used[name] > 1:
                name = f"{name} ({used[name]})"
            result.append(
                SavedKey(
                    item["id"], name, credential_id, hint, item["id"] == active and not self._env(document),
                    alias,
                )
            )
        return result

    @staticmethod
    def _env(document: dict) -> bool:
        return document["active"] == ENVIRONMENT

    async def get(self) -> Credential | None:
        key_id = self.active_id()
        if key_id is None:
            return None
        value = self._secret(self._store(), key_id)
        if not value:
            raise BackendError(
                BackendFailure(ErrorCategory.CREDENTIALS, "empty_saved_key", "Selected saved credential is empty.")
            )
        alias = next((item["name"] for item in self._read()["keys"] if item["id"] == key_id), None)
        return Credential("saved", value, _safe_alias(alias, value))

    def present(self) -> bool:
        return self.active_id() is not None

    # -- changing ----------------------------------------------------------------------------------------------
    def save(self, secret: str, name: str | None = None) -> str:
        """Save a key (or rename and reselect an already saved one) and make it the active key; returns its id."""
        value = secret.strip()
        if not value:
            raise BackendError(
                BackendFailure(ErrorCategory.CREDENTIALS, "empty_saved_key", "An empty API key cannot be saved.")
            )
        label = _clean_name(name)
        if label and value in label:
            raise ValidationError("A key name cannot contain the API key.")
        store = self._store()
        document = self._read()
        keys = self._ids(store, document)
        digest = key_identity(value)[0]
        for item in keys:
            existing = self._secret(store, item["id"])
            if existing and key_identity(existing)[0] == digest:
                if label:
                    item["name"] = label
                self._write({"active": item["id"], "keys": keys})
                return item["id"]
        key_id = MAIN if not keys else secrets.token_hex(4)
        store.set_password(SERVICE, _account(key_id), value)
        keys.append({"id": key_id, "name": label})
        self._write({"active": key_id, "keys": keys})
        return key_id

    def select(self, choice: str) -> None:
        """Make a saved key (by id) or the environment key (``ENVIRONMENT``) the active one."""
        store = self._store()
        document = self._read()
        keys = self._ids(store, document)
        if choice != ENVIRONMENT and all(k["id"] != choice for k in keys):
            raise ValidationError("That key is not saved.")
        self._write({"active": choice, "keys": keys})

    def rename(self, key_id: str, name: str | None) -> None:
        store = self._store()
        document = self._read()
        keys = self._ids(store, document)
        label = _clean_name(name)
        for item in keys:
            if item["id"] == key_id:
                secret = self._secret(store, key_id)
                if label and secret and secret in label:
                    raise ValidationError("A key name cannot contain the API key.")
                item["name"] = label
                self._write({"active": document["active"], "keys": keys})
                return
        raise ValidationError("That key is not saved.")

    def remove(self, key_id: str | None = None) -> None:
        """Remove one saved key (the active one by default); nothing to remove is not an error."""
        store = self._store()
        document = self._read()
        keys = self._ids(store, document)
        target = key_id if key_id is not None else self.active_id()
        if target is None or all(k["id"] != target for k in keys):
            return
        if store.get_password(SERVICE, _account(target)) is not None:
            store.delete_password(SERVICE, _account(target))
        keys = [k for k in keys if k["id"] != target]
        active = document["active"]
        if active == target or (active != ENVIRONMENT and all(k["id"] != active for k in keys)):
            active = keys[0]["id"] if keys else None
        self._write({"active": active, "keys": keys})


class OptionalSavedCredentials:
    """Defer desktop vault discovery until a worker needs credentials, never a Qt getter."""

    def __init__(self, saved: SavedCredentials):
        self.saved = saved

    def environment_preferred(self) -> bool:
        return bool(getattr(self.saved, "environment_preferred", lambda: False)())

    async def get(self) -> Credential | None:
        if not await asyncio.to_thread(self.saved.available):
            return None
        return await self.saved.get()


def credential_status(environ: Mapping[str, str] | None = None, saved: SavedCredentials | None = None) -> dict:
    """Which key cloud checks would select and every key available, without returning any secret.

    ``selected`` is ``saved``, ``environment`` or ``none``; ``keys`` lists the saved keys and ``environment``
    describes ``DRAWTHINGS_API_KEY`` (present, hint, credential id) so the UI can show and switch between them.
    """
    environ = os.environ if environ is None else environ
    env_value = environ.get(KEY, "").strip() if KEY in environ else None
    environment = {"present": env_value is not None, "credential_id": None, "hint": None}
    if env_value:
        environment["credential_id"], environment["hint"] = key_identity(env_value)
    store = saved or SavedCredentials()
    try:
        keys = store.entries()
        prefer_environment = store.environment_preferred()
    except SecureStorageUnavailable:
        keys, prefer_environment, state = [], False, "unavailable"
    else:
        state = "present" if keys else "absent"
    if keys and not (prefer_environment and environment["present"]):
        selected = "saved"
    elif environment["present"]:
        selected = "environment"
    else:
        selected = "none"
    return {"selected": selected, "saved": state, "keys": tuple(keys), "environment": environment}
