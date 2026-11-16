"""Field checks shared by the services' request schemas.

Each function returns the value unchanged or raises ValueError with a message
written for the person filling in the form. None passes through, so the same
function serves required and optional fields.
"""

import re
import unicodedata
from datetime import date

# Words joined by spaces, periods, apostrophes or hyphens: "Mary Ann", "O'Brien", "J. R. Smith", "Jr."
# Matched against _shape(), where every letter of any script has become "L".
_NAME_SHAPE = re.compile(r"L+(?:[ .'’-]+L+)*\.?")
_PLACE_SHAPE = re.compile(r"L[L .,'’()-]*")
_PHONE = re.compile(r"\+?[0-9 ().-]+")
_POSTAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 -]*")
EARLIEST_DATE = date(1900, 1, 1)


def _shape(value: str) -> str:
    """Letters and their combining marks (needed by Devanagari, Arabic, ...) become "L"; other characters stay."""
    return "".join("L" if unicodedata.category(ch)[0] in "LM" else ch for ch in value)


def person_name(value: str | None) -> str | None:
    if value is not None and not _NAME_SHAPE.fullmatch(_shape(value)):
        raise ValueError("use letters only; spaces, hyphens, apostrophes and periods are allowed between them")
    return value


def place_name(value: str | None) -> str | None:
    if value is not None and not (value[:1].isalpha() and _PLACE_SHAPE.fullmatch(_shape(value))):
        raise ValueError("must start with a letter and contain no digits or symbols")
    return value


def phone_number(value: str | None) -> str | None:
    if value is None:
        return value
    digits = sum(ch.isdigit() for ch in value)
    if not _PHONE.fullmatch(value) or not 7 <= digits <= 15:
        raise ValueError("enter 7 to 15 digits; a leading + and spaces, hyphens, periods or brackets are allowed")
    return value


def postal_code(value: str | None) -> str | None:
    if value is not None and (len(value) < 2 or not _POSTAL.fullmatch(value)):
        raise ValueError("use 2 to 20 letters or digits; spaces and hyphens are allowed")
    return value


def has_letter_or_digit(value: str | None) -> str | None:
    if value is not None and not any(ch.isalnum() for ch in value):
        raise ValueError("must contain at least one letter or digit")
    return value


def not_before_1900(value: date | None) -> date | None:
    if value is not None and value < EARLIEST_DATE:
        raise ValueError("cannot be before 1900")
    return value


def past_date(value: date | None) -> date | None:
    not_before_1900(value)
    if value is not None and value > date.today():
        raise ValueError("cannot be in the future")
    return value
