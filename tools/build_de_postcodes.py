"""Create SEDO's small offline postcode index from OpenPLZ API street data.

Usage: python tools/build_de_postcodes.py /path/to/streets.updated.csv
The source CSV is deliberately not stored with the application.
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def main(source: Path) -> None:
    places = defaultdict(set)
    with source.open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            postcode, locality = row['PostalCode'].strip(), row['Locality'].strip()
            if re.fullmatch(r'[0-9]{5}', postcode) and locality and len(locality) <= 100:
                places[postcode].add(locality)
    destination = Path(__file__).resolve().parent.parent / 'sedo' / 'postcode_data' / 'de_postcodes.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({code: sorted(names, key=str.casefold)
        for code, names in sorted(places.items())}, ensure_ascii=False,
        separators=(',', ':')) + '\n', encoding='utf-8')
    print(f'{len(places)} postcodes; {sum(map(len, places.values()))} postcode/place pairs -> {destination}')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python tools/build_de_postcodes.py streets.updated.csv')
    main(Path(sys.argv[1]))
