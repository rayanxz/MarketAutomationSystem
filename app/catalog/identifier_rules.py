from __future__ import annotations

import re

PRODUCT_CODE_PATTERN = r"^[A-Za-z0-9-]+$"
BARCODE_PATTERN = r"^[0-9]+$"

PRODUCT_CODE_ERROR = "الرمز يجب أن يحتوي على أحرف إنجليزية وأرقام والشرطة (-) فقط."
BARCODE_ERROR = "الباركود يجب أن يحتوي على أرقام إنجليزية (0-9) فقط."

_PRODUCT_CODE_RE = re.compile(PRODUCT_CODE_PATTERN)
_BARCODE_RE = re.compile(BARCODE_PATTERN)


def is_valid_product_code(value: str) -> bool:
    return bool(_PRODUCT_CODE_RE.fullmatch((value or "").strip()))


def is_valid_barcode(value: str) -> bool:
    return bool(_BARCODE_RE.fullmatch((value or "").strip()))
