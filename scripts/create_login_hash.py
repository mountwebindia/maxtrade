import getpass
import hashlib
import secrets


def main() -> None:
    password = getpass.getpass("New dashboard password (at least 16 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 16 or password != confirmation:
        raise SystemExit("Passwords must match and contain at least 16 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000).hex()
    print(f'MAXTRADE_PASSWORD_HASH = "pbkdf2_sha256$600000${salt.hex()}${digest}"')


if __name__ == "__main__":
    main()