from __future__ import annotations


SEGMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "start_sec",
        "end_sec",
        "title",
        "description",
        "score",
        "category",
        "color",
        "reason",
    ],
    "properties": {
        "start_sec": {"type": "number", "minimum": 0},
        "end_sec": {"type": "number", "exclusiveMinimum": 0},
        "title": {"type": "string", "minLength": 1, "maxLength": 100},
        "description": {"type": "string", "maxLength": 500},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "category": {"type": "string", "maxLength": 40},
        "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
        "reason": {"type": "string", "maxLength": 500},
    },
}


ANALYSIS_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clips"],
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "description",
                    "score",
                    "category",
                    "color",
                    "segments",
                ],
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 100},
                    "description": {"type": "string", "maxLength": 500},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                    "category": {"type": "string", "maxLength": 40},
                    "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                    "segments": {"type": "array", "minItems": 1, "items": SEGMENT_SCHEMA},
                },
            },
        },
        "segments": {"type": "array", "items": SEGMENT_SCHEMA},
    },
}
