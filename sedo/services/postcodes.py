"""Offline German postcode hints; no customer address leaves this installation."""
import json
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _index():
    path = Path(__file__).resolve().parent.parent / 'postcode_data' / 'de_postcodes.json'
    return json.loads(path.read_text(encoding='utf-8'))


def cities_for_postcode(postcode):
    """Return known locality names, or an empty tuple for unknown/invalid codes."""
    if not isinstance(postcode, str) or not re.fullmatch(r'[0-9]{5}', postcode):
        return ()
    return tuple(_index().get(postcode, ()))
