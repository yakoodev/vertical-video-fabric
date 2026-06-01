from __future__ import annotations


ANALYSIS_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "items": {
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
            },
        },
    },
}
