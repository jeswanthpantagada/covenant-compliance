"""
Date parsing, extraction, and normalization utilities for Credit Agreements.

Handles:
- ISO dates (YYYY-MM-DD)
- Textual contract dates (e.g., 'October 28, 2020', 'June 23, 2023')
- Fiscal quarter identifiers (e.g., 'Q1 2023', '2023-Q3', '2Q22', '4Q2021')
- Preamble date extraction from agreement / amendment text
- Absolute rejection of invented or assumed dates
"""

import re
from datetime import datetime
from typing import Optional

MONTH_MAP = {
    "january": "01", "jan": "01",
    "february": "02", "feb": "02",
    "march": "03", "mar": "03",
    "april": "04", "apr": "04",
    "may": "05",
    "june": "06", "jun": "06",
    "july": "07", "jul": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09", "sept": "09",
    "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "december": "12", "dec": "12",
}

QUARTER_END_MONTH_DAY = {
    "1": ("03", "31"),
    "2": ("06", "30"),
    "3": ("09", "30"),
    "4": ("12", "31"),
}


def parse_iso_date(text: str) -> Optional[str]:
    """Validates and returns YYYY-MM-DD if string is a valid ISO date."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        dt = datetime(year, month, day)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_textual_date(text: str) -> Optional[str]:
    """
    Parses textual dates like 'October 28, 2020' or '28 October 2020' into ISO 'YYYY-MM-DD'.
    """
    if not text or not isinstance(text, str):
        return None

    # Pattern 1: Month Day, Year (e.g. October 28, 2020 or Oct. 28, 2020)
    m1 = re.search(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if m1:
        m_name = m1.group(1).lower()
        day = int(m1.group(2))
        year = int(m1.group(3))
        if m_name in MONTH_MAP:
            month = int(MONTH_MAP[m_name])
            try:
                return datetime(year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Pattern 2: Day Month Year (e.g. 28 October 2020)
    m2 = re.search(r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", text)
    if m2:
        day = int(m2.group(1))
        m_name = m2.group(2).lower()
        year = int(m2.group(3))
        if m_name in MONTH_MAP:
            month = int(MONTH_MAP[m_name])
            try:
                return datetime(year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                pass

    return None


def extract_date_from_text(text: str) -> Optional[str]:
    """
    Scans document preamble or recitals for explicit contract execution or effective dates.
    Never invents or assumes a date if none is found.
    """
    if not text:
        return None

    # Common contract date patterns
    preamble_patterns = [
        r"(?:dated\s+as\s+of|effective\s+as\s+of|entered\s+into\s+as\s+of|made\s+as\s+of)\s+([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})",
        r"(?:dated\s+as\s+of|effective\s+as\s+of|entered\s+into\s+as\s+of|made\s+as\s+of)\s+(\d{1,2}\s+[A-Za-z]+,?\s+\d{4})",
        r"(?:dated\s+as\s+of|effective\s+as\s+of|entered\s+into\s+as\s+of|made\s+as\s+of)\s+(\d{4}-\d{2}-\d{2})",
    ]

    for pat in preamble_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw_match = m.group(1).strip()
            iso = parse_iso_date(raw_match) or parse_textual_date(raw_match)
            if iso:
                return iso

    return None


def quarter_to_date(quarter_str: str) -> Optional[str]:
    """
    Converts quarter representations into the exact calendar quarter end date:
    - 'Q1 2023', '2023-Q1', '2023Q1', '1Q23', '1Q2023' -> '2023-03-31'
    - 'Q2 2023', '2023-Q2', '2023Q2', '2Q23', '2Q2023' -> '2023-06-30'
    - 'Q3 2023', '2023-Q3', '2023Q3', '3Q23', '3Q2023' -> '2023-09-30'
    - 'Q4 2023', '2023-Q4', '2023Q4', '4Q23', '4Q2023' -> '2023-12-31'
    """
    if not quarter_str or not isinstance(quarter_str, str):
        return None

    q_str = quarter_str.strip().upper()

    # Pattern: 2023-Q1, 2023Q1, 2023 Q1
    m1 = re.match(r"^(\d{4})[-\s]?Q([1-4])$", q_str)
    if m1:
        year = m1.group(1)
        q_num = m1.group(2)
        mm, dd = QUARTER_END_MONTH_DAY[q_num]
        return f"{year}-{mm}-{dd}"

    # Pattern: Q1 2023, Q1-2023, Q12023
    m2 = re.match(r"^Q([1-4])[-\s]?(\d{4})$", q_str)
    if m2:
        q_num = m2.group(1)
        year = m2.group(2)
        mm, dd = QUARTER_END_MONTH_DAY[q_num]
        return f"{year}-{mm}-{dd}"

    # Pattern: 1Q23, 1Q2023
    m3 = re.match(r"^([1-4])Q(\d{2,4})$", q_str)
    if m3:
        q_num = m3.group(1)
        yr_raw = m3.group(2)
        year = f"20{yr_raw}" if len(yr_raw) == 2 else yr_raw
        mm, dd = QUARTER_END_MONTH_DAY[q_num]
        return f"{year}-{mm}-{dd}"

    return None


def normalize_date_or_quarter(input_val: str) -> str:
    """
    Normalizes any date or quarter expression into an ISO YYYY-MM-DD date.
    Raises ValueError if the input cannot be deterministically resolved.
    """
    if not input_val or not isinstance(input_val, str):
        raise ValueError(f"Date input cannot be empty or non-string: {input_val}")

    val = input_val.strip()

    # 1. ISO date check
    iso = parse_iso_date(val)
    if iso:
        return iso

    # 2. Quarter string check
    q_date = quarter_to_date(val)
    if q_date:
        return q_date

    # 3. Textual date check
    txt_date = parse_textual_date(val)
    if txt_date:
        return txt_date

    raise ValueError(
        f"Unable to parse or normalize date '{input_val}'. "
        f"Must be a valid ISO date (YYYY-MM-DD), textual date, or fiscal quarter (e.g. 'Q3 2023')."
    )
