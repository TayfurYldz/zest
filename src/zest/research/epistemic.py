"""Practical epistemic classes for Research context. Not a philosophical ontology.

Epistemic class is not authority. A model cannot relabel HYPOTHESIS as fact,
and model output cannot create an authoritative Observation.
"""

from __future__ import annotations

from enum import Enum


class EpistemicClass(Enum):
    """How a context item should be treated. Not a trust or promotion rank."""

    AUTHORITATIVE_FACT = "AUTHORITATIVE_FACT"
    OBSERVATION = "OBSERVATION"
    DERIVED_FACT = "DERIVED_FACT"
    INFERRED = "INFERRED"
    HYPOTHESIS = "HYPOTHESIS"
    NEGATIVE_EVIDENCE = "NEGATIVE_EVIDENCE"
    PROCEDURAL = "PROCEDURAL"
    UNTRUSTED_EXTERNAL = "UNTRUSTED_EXTERNAL"
    UNKNOWN = "UNKNOWN"


INSTRUCTION_TRUSTED_CLASSES = frozenset()
"""No ResearchContext item may issue system/research instructions."""
