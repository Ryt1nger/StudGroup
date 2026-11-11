"""Canonical username for RANEPA LK/SDO, shared by saving and crawling."""


def normalize_sdo_login(value: str | None) -> str | None:
    """An email-shaped input is a username: keep only the part before the first @."""
    if value is None:
        return None
    login = " ".join(value.split()).split("@", 1)[0].strip()
    if len(login) > 80:
        raise ValueError("too_long")
    return login or None
