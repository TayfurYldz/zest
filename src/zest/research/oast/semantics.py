"""Research-owned OAST semantic identifiers.

No application, persistence, runtime, authorization, or Worker authority
lives here. These constants describe research family/evaluator identity only.
"""

from __future__ import annotations


OAST_CALLBACK_EVALUATION_STRATEGY = "oast.callback.v1"

FAMILY_SSRF = "SSRF_SERVER_SIDE_FETCH"
FAMILY_XXE = "BLIND_XXE"
FAMILY_XSS = "BLIND_XSS"
FAMILY_WEBHOOK = "WEBHOOK_CALLBACK"

OAST_EXECUTABLE_FAMILIES = frozenset(
    {
        FAMILY_SSRF,
        FAMILY_XXE,
        FAMILY_XSS,
        FAMILY_WEBHOOK,
    }
)
