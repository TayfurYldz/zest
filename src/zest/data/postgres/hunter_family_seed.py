"""Canonical seed rows for the HunterFamily registry."""

SEED_FAMILIES = [
    {
        "family_id": "hf-object-authz",
        "name": "OBJECT_AUTHORIZATION",
        "target_node_kinds": ["HTTP_OPERATION", "RESOURCE_INSTANCE_CANDIDATE"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "Object authorization boundary on {origin}{path} "
            "may allow cross-owner access to {resource_id}."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["HTTP_AUTHORIZATION_DIFFERENTIAL"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-workflow-trans",
        "name": "WORKFLOW_STATE_TRANSITION",
        "target_node_kinds": ["WORKFLOW_TRANSITION"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "Workflow transition {transition} on {resource_id} at {origin}{path} "
            "may lack authorization check."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["HTTP_STATE_TRANSITION_AUTHORIZATION"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-exposed-api-spec",
        "name": "EXPOSED_API_SPEC",
        "target_node_kinds": ["API_SPEC"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "API specification at {canonical_key} documents endpoint surface "
            "that may be wider than observed access controls."
        ),
        "evidence_requirements": {
            "required_fact_kinds": ["API_SPEC"],
        },
        "validation_tier": "V2",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-unprotected-hostname",
        "name": "UNPROTECTED_HOSTNAME",
        "target_node_kinds": ["HOSTNAME"],
        "preconditions": {
            "scope_classification": "IN_SCOPE",
            "absent_edge_kind": "OBSERVED_UNDER",
        },
        "claim_template": (
            "Hostname {canonical_key} is in scope but has no observed "
            "active probe coverage yet."
        ),
        "evidence_requirements": {"required_edge_kind": "OBSERVED_UNDER"},
        "validation_tier": "V2",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-tech-cve-surface",
        "name": "TECH_KNOWN_CVE_SURFACE",
        "target_node_kinds": ["TECH"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "Technology {technology} at {canonical_key} is a candidate "
            "for known-vulnerability class verification."
        ),
        "evidence_requirements": {"required_fact_kinds": ["TECH"]},
        "validation_tier": "V2",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-sqli",
        "name": "SQL_INJECTION",
        "target_node_kinds": ["HTTP_OPERATION", "FORM", "API_SPEC"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "Input-bearing surface {canonical_key} should receive bounded SQL "
            "parser-differential experiments before being marked covered."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["MUTATION_MATRIX_RESULT"],
            "required_controls": ["secure_fixture", "deceptive_fixture", "read_back"],
            "required_matrix_dimensions": ["input_vector", "encoding", "parser_delta"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-ssti",
        "name": "SERVER_SIDE_TEMPLATE_INJECTION",
        "target_node_kinds": ["HTTP_OPERATION", "FORM", "API_SPEC"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "Template-rendering surface {canonical_key} should receive bounded "
            "SSTI parser-differential experiments before being marked covered."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["MUTATION_MATRIX_RESULT"],
            "required_controls": ["secure_fixture", "deceptive_fixture"],
            "required_matrix_dimensions": ["template_engine_probe", "encoding"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-lfi-rfi",
        "name": "FILE_INCLUDE_AND_PATH_TRAVERSAL",
        "target_node_kinds": ["HTTP_OPERATION", "FORM", "API_SPEC"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "File/path-influencing surface {canonical_key} should receive "
            "bounded traversal/include experiments with negative controls."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["MUTATION_MATRIX_RESULT"],
            "required_controls": ["safe_path_control", "deceptive_status_control"],
            "required_matrix_dimensions": ["path_vector", "encoding", "normalization"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-mass-assignment",
        "name": "MASS_ASSIGNMENT",
        "target_node_kinds": ["HTTP_OPERATION", "API_SPEC"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "Writable object surface {canonical_key} should receive bounded "
            "mass-assignment experiments with independent read-back."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["MUTATION_MATRIX_RESULT"],
            "required_controls": ["read_back", "role_boundary_control"],
            "required_matrix_dimensions": ["field_family", "role", "state_change"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-jwt-crypto",
        "name": "JWT_CRYPTO_AND_CLAIM_CONFUSION",
        "target_node_kinds": ["HTTP_OPERATION", "API_SPEC", "TECH"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "Token-bearing surface {canonical_key} should receive bounded JWT "
            "algorithm, key, and claim-confusion experiments."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["MUTATION_MATRIX_RESULT"],
            "required_controls": ["valid_token_control", "invalid_token_control"],
            "required_matrix_dimensions": ["algorithm", "key_source", "claim"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-cors",
        "name": "CORS_CREDENTIAL_EXFILTRATION_CHAIN",
        "target_node_kinds": ["ORIGIN", "HTTP_OPERATION", "API_SPEC"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "Cross-origin surface {canonical_key} should receive credentialed "
            "CORS exfiltration-chain checks before being marked covered."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["MUTATION_MATRIX_RESULT"],
            "required_controls": ["non_credentialed_control", "sensitive_endpoint_read_back"],
            "required_matrix_dimensions": ["origin_variant", "credentials", "data_sink"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-graphql",
        "name": "GRAPHQL_AUTHORIZATION_AND_INJECTION",
        "target_node_kinds": ["API_SPEC", "HTTP_OPERATION"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "GraphQL-capable surface {canonical_key} should receive resolver, "
            "object-authorization, and injection experiments."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["MUTATION_MATRIX_RESULT"],
            "required_controls": ["introspection_control", "role_boundary_control"],
            "required_matrix_dimensions": ["operation_kind", "resolver", "identity"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-dom-taint",
        "name": "DOM_TAINT_AND_CLIENT_SIDE_EXECUTION",
        "target_node_kinds": ["JS_BUNDLE", "PAGE_STATE", "FORM"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "Client-side surface {canonical_key} should receive DOM source-to-sink "
            "taint experiments with detection-token rotation."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["MUTATION_MATRIX_RESULT"],
            "required_controls": ["dom_marker_control", "detection_rotation"],
            "required_matrix_dimensions": ["source", "sink", "execution_token"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-ai-llm-target",
        "name": "AI_LLM_PROMPT_INJECTION_AND_TOOL_ABUSE",
        "target_node_kinds": ["HTTP_OPERATION", "API_SPEC", "PAGE_STATE"],
        "preconditions": {"scope_classification": "IN_SCOPE"},
        "claim_template": (
            "AI/LLM-backed surface {canonical_key} should receive prompt-injection, "
            "context-leakage, and tool-abuse experiments with metamorphic controls."
        ),
        "evidence_requirements": {
            "required_observation_kinds": ["MODEL_TARGET_MUTATION_RESULT"],
            "required_controls": ["benign_prompt_control", "metamorphic_variant", "tool_denial_control"],
            "required_matrix_dimensions": ["instruction_channel", "retrieval_context", "tool_boundary"],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-http-smuggling-desync",
        "name": "HTTP_REQUEST_SMUGGLING_DESYNC",
        "target_node_kinds": ["HTTP_OPERATION", "SERVICE", "TECH"],
        "preconditions": {
            "scope_classification": "IN_SCOPE",
            "required_attribute_any": {
                "protocol_surface_signals": [
                    "reverse_proxy",
                    "http2",
                    "h2c_upgrade",
                    "connection_reuse",
                    "front_backend_split",
                ],
            },
        },
        "claim_template": (
            "Protocol surface {canonical_key} has parser-boundary evidence "
            "supporting request-smuggling/desync specialist planning."
        ),
        "evidence_requirements": {
            "protocol_lane": "http_request_smuggling_desync",
            "required_surface_signals": [
                "reverse_proxy",
                "http2",
                "h2c_upgrade",
                "connection_reuse",
                "front_backend_split",
            ],
            "required_controls": [
                "single_parser_control",
                "connection_close_control",
                "deceptive_proxy_control",
            ],
            "required_protocol_dimensions": [
                "frontend_protocol",
                "backend_protocol",
                "normalization_boundary",
            ],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
    {
        "family_id": "hf-cache-poison-deception",
        "name": "HTTP_CACHE_POISONING_DECEPTION",
        "target_node_kinds": ["HTTP_OPERATION", "ORIGIN", "SERVICE", "TECH"],
        "preconditions": {
            "scope_classification": "IN_SCOPE",
            "required_attribute_any": {
                "protocol_surface_signals": [
                    "cdn",
                    "edge_cache",
                    "reverse_proxy",
                    "cache_header",
                    "vary_header",
                ],
            },
        },
        "claim_template": (
            "Cache/proxy surface {canonical_key} has normalization evidence "
            "supporting cache poisoning/deception specialist planning."
        ),
        "evidence_requirements": {
            "protocol_lane": "http_cache_poisoning_deception",
            "required_surface_signals": [
                "cdn",
                "edge_cache",
                "reverse_proxy",
                "cache_header",
                "vary_header",
            ],
            "required_controls": [
                "non_cacheable_control",
                "vary_control",
                "deceptive_cache_control",
            ],
            "required_protocol_dimensions": [
                "cache_key_dimension",
                "cache_behavior",
                "proxy_layer",
            ],
        },
        "validation_tier": "V3",
        "enabled": True,
        "version": 1,
    },
]
