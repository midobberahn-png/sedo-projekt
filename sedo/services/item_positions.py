"""Fortlaufende Positionsnummern für Rechnungen, Angebote und Vorlagen."""


def normalize_item_positions(items) -> None:
    for idx, item in enumerate(items, start=1):
        if item.position != idx:
            item.position = idx
            item.save(update_fields=['position', 'updated_at'])
