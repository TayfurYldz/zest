"""Application-layer errors. Not Core policy DENY. Not Evidence."""


class ApplicationError(Exception):
    """Use-case coordination failure."""


class IngestionRejected(ApplicationError):
    """Invocation/result was not admitted. Not a vulnerability verdict."""


class OrchestrationIntegrityError(ApplicationError):
    """Persisted orchestration configuration or fingerprint is inconsistent."""
