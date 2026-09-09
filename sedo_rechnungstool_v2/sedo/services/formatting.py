"""Formatierungshelfer für die Service-Schicht."""
from __future__ import annotations

from decimal import Decimal


def euro(value) -> str:
    """Formatiert einen Betrag als deutschen Euro-String, z. B. '1.234,56 €'."""
    return f"{value:,.2f} €".replace(',', 'X').replace('.', ',').replace('X', '.')


# Rückwärtskompatibler Alias – wurde intern früher als services._euro genutzt.
_euro = euro
