import logging
import os

from cryptography.fernet import Fernet, MultiFernet
from django.db import models

logger = logging.getLogger(__name__)


def get_fernet():
    keys_str = os.environ.get("EVENTYAY_VOXBENTO_FERNET_KEYS", "")

    if not keys_str:
        import base64
        import hashlib

        from django.conf import settings

        # Derive a 32-byte url-safe base64 key from Django's SECRET_KEY
        secret = getattr(settings, "SECRET_KEY", "fallback-secret-key").encode("utf-8")
        derived_key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
        keys_str = derived_key.decode("utf-8")

    keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    if not keys:
        return None

    try:
        fernets = [Fernet(k.encode("utf-8")) for k in keys]
        return MultiFernet(fernets)
    except Exception as e:
        logger.error(f"Failed to initialize MultiFernet: {e}")
        return None


class EncryptedTextField(models.TextField):
    """
    A TextField that encrypts its contents using MultiFernet.
    Requires the EVENTYAY_VOXBENTO_FERNET_KEYS environment variable to be set.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value

        fernet = get_fernet()
        if not fernet:
            logger.error("EVENTYAY_VOXBENTO_FERNET_KEYS not configured. Cannot encrypt data.")
            raise ValueError("Encryption keys not configured")

        if isinstance(value, str):
            value = value.encode("utf-8")

        encrypted_bytes = fernet.encrypt(value)
        return encrypted_bytes.decode("utf-8")

    def from_db_value(self, value, expression, connection):
        if not value:
            return value

        fernet = get_fernet()
        if not fernet:
            logger.error("EVENTYAY_VOXBENTO_FERNET_KEYS not configured. Cannot decrypt data.")
            raise ValueError("Decryption keys not configured")

        try:
            decrypted_bytes = fernet.decrypt(value.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except Exception:
            # Fallback to return the original value. This is necessary during the
            # data migration phase when existing plaintext values are read from the DB
            # before they are re-saved as ciphertext.
            return value
