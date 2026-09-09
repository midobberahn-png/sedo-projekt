from __future__ import annotations

from django.db.utils import OperationalError, ProgrammingError


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '') if request else ''
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') if request else None


def write_audit(request, action: str, obj=None, message: str = '', metadata: dict | None = None,
                object_type: str = '', object_id: str = '', object_repr: str = ''):
    """Schreibt ein Audit-Log, ohne die eigentliche App-Aktion zu gefährden."""
    try:
        from .models import AuditLog
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False):
            user = None
        if obj is not None:
            object_type = object_type or obj.__class__.__name__
            object_id = object_id or str(getattr(obj, 'pk', '') or '')
            object_repr = object_repr or str(obj)
        AuditLog.objects.create(
            user=user,
            action=action,
            object_type=object_type or '',
            object_id=object_id or '',
            object_repr=(object_repr or '')[:255],
            message=message or '',
            ip_address=_client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '') if request else '')[:2000],
            metadata=metadata or {},
        )
    except (OperationalError, ProgrammingError):
        return
    except Exception:
        return
