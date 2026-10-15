"""Bounded spreadsheet/CSV imports; raw documents only reach the parser worker."""
import csv
import io
import math
from pathlib import PurePosixPath
import zipfile

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 10_000
MAX_COLUMNS = 200
MAX_CELL_CHARS = 32_768
MAX_EXPANDED_BYTES = 64 * 1024 * 1024


def read_upload(upload, limit=MAX_UPLOAD_BYTES):
    if getattr(upload, 'size', 0) > limit:
        raise ValueError('Die Datei ist zu groß. Bitte einen kleineren Import verwenden.')
    try:
        upload.seek(0)
    except (OSError, AttributeError):
        pass
    data = upload.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError('Die Datei ist leer oder überschreitet die zulässige Größe.')
    return data


def read_dataframe(upload, header=0):
    import pandas as pd
    from .document_probe import DocumentProbeError, run_operation
    data = read_upload(upload)
    extension = PurePosixPath(getattr(upload, 'name', '')).suffix.lower()
    if extension not in {'.xlsx', '.xls', '.csv'}:
        raise ValueError('Bitte eine Excel- oder CSV-Datei auswählen.')
    try:
        result = run_operation('table_rows', [data], extension=extension, header=header)
    except DocumentProbeError as exc:
        raise ValueError('Die Tabelle ist beschädigt, zu umfangreich oder konnte nicht sicher gelesen werden.') from exc
    if not isinstance(result, dict):
        raise ValueError('Die Tabelle liefert kein gültiges Importformat.')
    columns, rows = result.get('columns'), result.get('rows')
    if (not isinstance(columns, list) or len(columns) > MAX_COLUMNS
            or not all(isinstance(c, str) and len(c) <= MAX_CELL_CHARS for c in columns)
            or not isinstance(rows, list) or len(rows) > MAX_ROWS
            or not all(isinstance(row, list) and len(row) == len(columns)
                       and all(_valid_result_cell(v) for v in row) for row in rows)):
        raise ValueError('Die Tabelle liefert kein gültiges Importformat.')
    return pd.DataFrame(rows, columns=columns, dtype=object)


def _valid_result_cell(value):
    if value is None:
        return True
    if isinstance(value, str):
        return len(value) <= MAX_CELL_CHARS
    if isinstance(value, (int, float, bool)):
        return not isinstance(value, float) or math.isfinite(value)
    return False


def _check_xlsx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = archive.infolist()
        if (len(files) > 2000 or sum(i.file_size for i in files) > MAX_EXPANDED_BYTES
                or len({i.filename for i in files}) != len(files)):
            raise ValueError('Spreadsheet archive exceeds limits')
        for member in files:
            path = PurePosixPath(member.filename)
            # zipfile normalizes backslashes to slashes on Windows. Inspect the
            # original ZIP entry too, before that normalization hides them.
            raw_name = getattr(member, 'orig_filename', member.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in raw_name
                    or '\x00' in raw_name
                    or member.flag_bits & 1
                    or member.file_size > 200 * max(member.compress_size, 1)):
                raise ValueError('Unsafe spreadsheet archive')


def _cell(value):
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        result = value
    elif hasattr(value, 'isoformat'):
        result = value.isoformat()
    else:
        result = str(value)
    if len(str(result)) > MAX_CELL_CHARS:
        raise ValueError('Spreadsheet cell exceeds limits')
    return result


def worker_rows(request, documents):
    """Runs only inside document_probe's bounded, isolated worker."""
    data, = documents
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('Spreadsheet exceeds limits')
    extension = request.get('extension')
    header = request.get('header', 0)
    if header not in (0, 1):
        raise ValueError('Unsupported header')
    if extension == '.csv':
        text = data.decode('utf-8-sig')
        csv.field_size_limit(MAX_CELL_CHARS)
        first = text.split('\n', 1)[0]
        reader = csv.reader(io.StringIO(text), delimiter=';' if ';' in first else ',')
        columns = next(reader, [])
        rows = []
        for row in reader:
            if not row:
                continue
            if len(rows) >= MAX_ROWS or len(row) > len(columns):
                raise ValueError('Spreadsheet exceeds row/column limits')
            rows.append(row + [None] * (len(columns) - len(row)))
    else:
        if extension == '.xlsx':
            _check_xlsx(data)
        elif extension != '.xls':
            raise ValueError('Unsupported spreadsheet')
        import pandas as pd
        frame = pd.read_excel(io.BytesIO(data), sheet_name=0, header=header,
                              dtype=object, nrows=MAX_ROWS + 1)
        columns = [str(c) for c in frame.columns]
        rows = frame.values.tolist()
    if len(columns) > MAX_COLUMNS or len(rows) > MAX_ROWS:
        raise ValueError('Spreadsheet exceeds row/column limits')
    return {'ok': True, 'columns': [_cell(str(c)) for c in columns],
            'rows': [[_cell(v) for v in row] for row in rows]}
