"""Preserve original email bytes whenever a durable business reference is saved."""
from django.db.models.signals import pre_save
from django.dispatch import receiver

from .models import MailDraft, MailMessage, Offer, WorkTask
from .services.mail_storage import pin_message


@receiver(pre_save, sender=Offer, dispatch_uid='mail_offer_source')
@receiver(pre_save, sender=WorkTask, dispatch_uid='mail_task_source')
def pin_business_reference(sender, instance, raw=False, **kwargs):
    if not raw and instance.source_mail_id:
        pin_message(instance.source_mail)


@receiver(pre_save, sender=MailDraft, dispatch_uid='mail_reply_source')
def pin_reply_source(sender, instance, raw=False, **kwargs):
    if not raw and instance.source_id:
        pin_message(instance.source)


@receiver(pre_save, sender=MailMessage, dispatch_uid='mail_customer_source')
def pin_customer_source(sender, instance, raw=False, **kwargs):
    if not raw and instance.pk and instance.customer_id:
        pin_message(instance)
