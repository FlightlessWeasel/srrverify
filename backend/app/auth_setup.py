"""Configure authentication for Game CRC Checker.

    python -m app.auth_setup            # enable / re-enroll (prompts)
    python -m app.auth_setup --show     # print the current otpauth URI + QR
    python -m app.auth_setup --disable  # turn auth off (keeps nothing)

Run from the backend/ directory.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys

from . import auth


def _print_enrollment(secret: str, username: str) -> None:
    uri = auth.totp_uri(secret, username)
    print("\nScan this in Google Authenticator / Authy / 1Password:\n")
    try:
        import io

        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        sys.stdout.write(buf.getvalue())
    except Exception as exc:  # noqa: BLE001 - QR is a convenience, not required
        print(f"(could not draw QR: {exc}; use the manual key below)")
    print(f"\notpauth URI : {uri}")
    print(f"Manual key  : {secret}")
    print(f"Account     : {username}\n")


def cmd_disable() -> None:
    cfg = auth.load_config()
    if not cfg:
        print("Auth was not configured. Nothing to do.")
        return
    cfg["enabled"] = False
    auth.save_config(cfg)
    print("Authentication disabled. The app now serves without a login.")


def cmd_show() -> None:
    cfg = auth.load_config()
    if not cfg.get("totp_secret") or not cfg.get("username"):
        print("Auth is not configured. Run `python -m app.auth_setup`.")
        sys.exit(1)
    state = "enabled" if cfg.get("enabled") else "disabled"
    print(f"Auth is currently {state} for user '{cfg['username']}'.")
    _print_enrollment(cfg["totp_secret"], cfg["username"])


def cmd_setup() -> None:
    cfg = auth.load_config()
    default_user = cfg.get("username") or "admin"

    # Headless path for servers / scripts.
    env_user = os.environ.get("GAMECRC_SETUP_USER")
    env_pw = os.environ.get("GAMECRC_SETUP_PASSWORD")
    interactive = not (env_user and env_pw)
    if not interactive:
        username, pw1 = env_user, env_pw
        if len(pw1) < 8:
            print("GAMECRC_SETUP_PASSWORD must be at least 8 characters.")
            sys.exit(1)
    else:
        username = input(f"Username [{default_user}]: ").strip() or default_user
        pw1 = getpass.getpass("Password: ")
        if len(pw1) < 8:
            print("Password must be at least 8 characters.")
            sys.exit(1)
        if pw1 != getpass.getpass("Confirm password: "):
            print("Passwords did not match.")
            sys.exit(1)

    keep = False
    if cfg.get("totp_secret") and interactive:
        keep = input("Keep the existing MFA secret? [y/N]: ").strip().lower() == "y"
    secret = cfg["totp_secret"] if keep else auth.generate_totp_secret()

    new_cfg = {
        "enabled": True,
        "username": username,
        "password_hash": auth.hash_password(pw1),
        "totp_secret": secret,
        "server_secret": cfg.get("server_secret") or secrets.token_bytes(32).hex(),
    }
    auth.save_config(new_cfg)

    print(f"\nSaved to {auth.AUTH_FILE}")
    if not keep:
        _print_enrollment(secret, username)
    if interactive:
        print("Verify a code now to be sure the clock is in sync.")
        try:
            code = input("Enter the current 6-digit code (blank to skip): ").strip()
        except EOFError:
            code = ""
        if code:
            ok = auth.verify_totp(secret, code) is not None
            print("MFA code OK." if ok else "MFA code did NOT verify — check device time.")
    print("\nRestart the server for the change to take effect.")


def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # QR blocks need UTF-8
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="Configure Game CRC Checker auth")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--disable", action="store_true", help="turn auth off")
    group.add_argument("--show", action="store_true", help="show current MFA enrollment")
    args = parser.parse_args(argv)

    if args.disable:
        cmd_disable()
    elif args.show:
        cmd_show()
    else:
        cmd_setup()


if __name__ == "__main__":
    main()
