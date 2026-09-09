from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .audit import write_audit


@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    write_audit(request, 'login', object_type='User', object_id=str(user.pk), object_repr=user.get_username(), message='Benutzer angemeldet')


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    if user:
        write_audit(request, 'logout', object_type='User', object_id=str(user.pk), object_repr=user.get_username(), message='Benutzer abgemeldet')


@receiver(user_login_failed)
def audit_login_failed(sender, credentials, request, **kwargs):
    username = credentials.get('username', '') if credentials else ''
    write_audit(request, 'security', object_type='Login', object_repr=username, message='Fehlgeschlagener Loginversuch')
