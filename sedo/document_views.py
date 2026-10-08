"""Authenticated in-app previews for locally generated business documents."""
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import stat
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_XML_PREVIEW_BYTES = 2 * 1024 * 1024


def _read_generated_document(path):
    """Bound reads to regular, non-symlink PDF/XML files in generated folders."""
    parts = PurePosixPath(path).parts
    if (not parts or parts[0] not in {'generated', 'dunning', 'cashbook'}
            or any(part in ('.', '..') for part in parts) or '\\' in path
            or any(ord(char) < 32 for char in path)):
        raise Http404('Dokument nicht gefunden.')
    relative = PurePosixPath(*parts)
    if relative.suffix.lower() not in {'.pdf', '.xml'}:
        raise Http404('Dieses Dokumentformat wird nicht angezeigt.')
    root = Path(settings.MEDIA_ROOT).resolve()
    target = root
    try:
        for part in parts:
            target = target / part
            if target.is_symlink():
                raise Http404('Dokument nicht gefunden.')
        target.resolve(strict=True).relative_to(root)
        descriptor = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DOCUMENT_BYTES:
                raise Http404('Dokument kann nicht innerhalb der Anzeigegrenzen gelesen werden.')
            data = handle.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise Http404('Dokument überschreitet die Anzeigegrenze.')
    except (OSError, ValueError):
        raise Http404('Dokument nicht gefunden.') from None
    return relative, data


def _back_url(request):
    candidate = request.GET.get('back') or request.META.get('HTTP_REFERER', '')
    if (candidate and len(candidate) <= 2048 and '\\' not in candidate
            and not any(ord(char) < 32 for char in candidate)
            and url_has_allowed_host_and_scheme(candidate, {request.get_host()},
                                               require_https=request.is_secure())):
        parsed = urlsplit(candidate)
        if parsed.path.startswith('/') and not parsed.path.startswith('//'):
            return parsed.path + ('?' + parsed.query if parsed.query else '')
    return reverse('sedo:invoice_list')


@login_required
@require_GET
@never_cache
def preview(request, path):
    relative, data = _read_generated_document(path)
    kind = relative.suffix.lower()
    mime = 'application/pdf' if kind == '.pdf' else 'application/xml'
    download = request.GET.get('download') == '1'
    inline = request.GET.get('inline') == '1'
    if inline and kind != '.pdf':
        raise Http404('XML wird ausschließlich als Text oder Download angezeigt.')
    if download or inline:
        if inline:
            from .document_probe import DocumentProbeError, validate_pdf
            try:
                validate_pdf(data)
            except DocumentProbeError:
                return HttpResponse('Das PDF konnte nicht sicher angezeigt werden. Bitte das Original herunterladen und prüfen.',
                                    status=409, content_type='text/plain; charset=utf-8')
        response = FileResponse(BytesIO(data), as_attachment=download,
                                filename=relative.name, content_type=mime)
        response['Content-Security-Policy'] = "default-src 'none'; object-src 'self'; frame-ancestors 'self'"
        response['X-Frame-Options'] = 'SAMEORIGIN'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    context = {
        'filename': relative.name,
        'is_pdf': kind == '.pdf',
        'back_url': _back_url(request),
        'document_url': reverse('sedo:document_preview', kwargs={'path': str(relative)}),
        'document_text': '',
        'preview_error': '',
    }
    if kind == '.xml':
        if len(data) > MAX_XML_PREVIEW_BYTES:
            context['preview_error'] = 'Diese XML-Datei ist für die Textvorschau zu groß. Das Original kann heruntergeladen werden.'
        else:
            # Never parse XML or insert it as markup. Django escapes this text.
            context['document_text'] = data.decode('utf-8-sig', errors='replace')
    response = render(request, 'sedo/document_preview.html', context)
    response['Content-Security-Policy'] = "frame-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    return response
