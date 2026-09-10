"""Gemeinsame fachliche Fehler, ohne zyklische Service-Importe."""


class WorkflowError(ValueError):
    """Fachliche Sperre; die Oberfläche zeigt eine verständliche Meldung."""


class BelegArchivFehler(WorkflowError):
    """Ein Original fehlt, ist verändert oder darf nicht überschrieben werden."""
