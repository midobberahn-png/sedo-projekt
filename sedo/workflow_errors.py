"""Shared business errors without importing the service package."""


class WorkflowError(ValueError):
    """A safe, user-facing business rule failure."""


class BelegArchivFehler(WorkflowError):
    """An immutable original is missing, modified or may not be overwritten."""
