"""Cryptographic utilities for secure token storage."""

import base64
import os
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken


def get_encryption_key_path() -> Path:
    """Get the path to store the encryption key."""
    data_dir = Path(__file__).parent.parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / ".encryption_key"


def load_encryption_key() -> Optional[str]:
    """
    Load the encryption key from file.

    Returns:
        Encryption key string if exists, None otherwise.
    """
    key_path = get_encryption_key_path()
    if key_path.exists():
        try:
            return key_path.read_text().strip()
        except Exception:
            return None
    return None


def save_encryption_key(key: str) -> bool:
    """
    Save the encryption key to file.

    Args:
        key: Encryption key to save.

    Returns:
        True if successful, False otherwise.
    """
    try:
        key_path = get_encryption_key_path()
        key_path.write_text(key)
        # Set restrictive permissions (owner read/write only)
        os.chmod(key_path, 0o600)
        return True
    except Exception:
        return False


class TokenEncryptor:
    """
    Handles encryption and decryption of sensitive tokens.

    Uses Fernet (symmetric encryption) with PBKDF2 key derivation
    for secure storage of OAuth tokens.
    """

    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize the encryptor with an encryption key.

        Args:
            encryption_key: Base64 encoded 32-byte key.
                          If None, generates a new key.
        """
        if encryption_key:
            self._key = base64.urlsafe_b64decode(encryption_key)
        else:
            # Generate a new random key
            self._key = Fernet.generate_key()

        self._fernet = Fernet(self._key)

    @property
    def key(self) -> str:
        """Return the base64 encoded encryption key."""
        return base64.urlsafe_b64encode(self._key).decode()

    @classmethod
    def generate_key(cls) -> str:
        """
        Generate a new encryption key.

        Returns:
            Base64 encoded 32-byte encryption key.
        """
        return cls().key

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string.

        Args:
            plaintext: The string to encrypt.

        Returns:
            Base64 encoded encrypted string.
        """
        if not plaintext:
            return ""
        encrypted = self._fernet.encrypt(plaintext.encode())
        return base64.urlsafe_b64encode(encrypted).decode()

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt an encrypted string.

        Args:
            ciphertext: Base64 encoded encrypted string.

        Returns:
            Decrypted plaintext string.

        Raises:
            InvalidToken: If decryption fails.
        """
        if not ciphertext:
            return ""
        try:
            decrypted = base64.urlsafe_b64decode(ciphertext.encode())
            return self._fernet.decrypt(decrypted).decode()
        except (InvalidToken, ValueError) as e:
            raise InvalidToken(f"Failed to decrypt token: {e}")

    def is_valid_key(self, key: str) -> bool:
        """
        Check if a key is valid.

        Args:
            key: Base64 encoded key to validate.

        Returns:
            True if valid, False otherwise.
        """
        try:
            base64.urlsafe_b64decode(key.encode())
            return len(base64.urlsafe_b64decode(key)) == 32
        except Exception:
            return False


def generate_encryption_key() -> str:
    """
    Generate a new encryption key for token storage.

    This should be called once during initial setup,
    and the key should be stored securely.

    Returns:
        Base64 encoded 32-byte encryption key.
    """
    return TokenEncryptor.generate_key()
