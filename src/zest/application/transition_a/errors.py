"""Transition A errors. Fail closed. Not vulnerability judgments."""


class TransitionAError(Exception):
    """Deterministic admission/normalization failure."""


class UnsupportedNormalizerError(TransitionAError):
    """No registered normalizer for the trusted capability/action."""


class MalformedNormalizedPayloadError(TransitionAError):
    """Canonical result cannot be normalized. Do not emit Observation."""
