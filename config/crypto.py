from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class CredentialEncryptionError(Exception):
    pass


def _fernet():
    key = settings.AI_CREDENTIAL_ENCRYPTION_KEY
    if not key:
        raise CredentialEncryptionError(
            "AI_CREDENTIAL_ENCRYPTION_KEY não está configurada."
        )
    try:
        return Fernet(key.encode())
    except (TypeError, ValueError) as error:
        raise CredentialEncryptionError(
            "AI_CREDENTIAL_ENCRYPTION_KEY possui formato inválido."
        ) from error


def encrypt_credential(value):
    if not value:
        raise CredentialEncryptionError("A credencial não pode ser vazia.")
    return _fernet().encrypt(value.encode()).decode()


def decrypt_credential(value):
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as error:
        raise CredentialEncryptionError(
            "Não foi possível descriptografar a credencial."
        ) from error
