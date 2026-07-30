"""Encryption for API keys held in the ``settings`` table.

The threat model is narrow and worth stating so the guarantee isn't oversold: this protects
against keys sitting in plaintext inside a database file that users are actively encouraged to
copy around as a backup. It does **not** protect against an attacker who can already read
``/data``, since the key material lives there too. Defending against that would require a
passphrase prompt on every boot, which contradicts the "``docker run`` and you're working"
requirement in PRD §2.3.
"""

from __future__ import annotations

import base64
import os
import secrets
import stat
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class DecryptionError(RuntimeError):
    """Raised when a stored secret cannot be decrypted with the current machine secret."""


def _restrict_permissions(path: Path) -> None:
    """Best-effort ``chmod 600`` equivalent.

    On POSIX this is exact. On Windows, ``os.chmod`` cannot express an ACL, so ``icacls`` is
    used to strip inherited access and grant the current user alone. A failure here is logged
    by the caller rather than fatal — an unreadable-by-others secret is desirable, not a
    correctness requirement.
    """
    if sys.platform == "win32":  # pragma: no cover - platform specific
        import subprocess

        user = os.environ.get("USERNAME")
        if not user:
            return
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            check=False,
        )
    else:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load_or_create_machine_secret(secret_path: Path) -> bytes:
    """Read the machine-local secret, generating it on first boot."""
    if secret_path.exists():
        data = secret_path.read_bytes().strip()
        if len(data) >= 32:
            return data
        # A truncated secret would silently produce a different key and make every stored
        # value undecryptable. Refuse rather than corrupt.
        raise DecryptionError(
            f"{secret_path} exists but is too short to be a valid secret. "
            "If this file was truncated, delete it and re-enter your API keys in Settings."
        )

    secret_path.parent.mkdir(parents=True, exist_ok=True)
    data = base64.urlsafe_b64encode(secrets.token_bytes(48))
    # Create with restrictive permissions from the outset, rather than writing then tightening.
    fd = os.open(str(secret_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    _restrict_permissions(secret_path)
    return data


class SecretBox:
    """Symmetric encryption bound to one machine secret."""

    def __init__(self, machine_secret: bytes) -> None:
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"quicklearn-settings-v1",
            info=b"fernet-key",
        ).derive(machine_secret)
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise DecryptionError(
                "Stored API key could not be decrypted — the .secret file in your data "
                "directory has changed. Re-enter your keys in Settings."
            ) from exc

    @classmethod
    def from_path(cls, secret_path: Path) -> SecretBox:
        return cls(load_or_create_machine_secret(secret_path))
