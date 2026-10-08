"""Gemeinsame fachliche Fehler, ohne zyklische Service-Importe."""


from ..workflow_errors import WorkflowError, BelegArchivFehler

__all__ = ['WorkflowError', 'BelegArchivFehler']
