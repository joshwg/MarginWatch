"""UI style helpers: translate domain values to visual attributes."""

import constants


def expiry_color(days: int) -> str:
    """Background colour for a row based on days until expiry."""
    if days < 0:
        return constants.EXPIRY_EXPIRED
    elif days <= 7:
        return constants.EXPIRY_WEEK
    elif days <= 14:
        return constants.EXPIRY_TWOWEEKS
    elif days <= 21:
        return constants.EXPIRY_THREEWEEKS
    elif days <= 28:
        return constants.EXPIRY_FOURWEEKS
    else:
        return constants.EXPIRY_DISTANT


def text_color(bg_hex: str) -> str:
    """Return '#000000' or '#ffffff' for readable contrast on bg_hex."""
    h = bg_hex.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return constants.TEXT_DARK if brightness > 155 else constants.TEXT_LIGHT
