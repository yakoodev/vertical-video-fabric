from __future__ import annotations


SEGMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "One source range inside a final clip plan. A clip may contain multiple "
        "ordered segments that together form an episode recap or standalone main story clip. For "
        "episodic fiction, keep each segment 12 to 75 seconds and use complete "
        "spoken lines with natural entry and exit points."
    ),
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
        "title": {"type": "string", "minLength": 1, "maxLength": 100, "description": "Russian segment title."},
        "description": {"type": "string", "maxLength": 500, "description": "Russian segment description."},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "category": {"type": "string", "maxLength": 40},
        "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
        "reason": {"type": "string", "maxLength": 500, "description": "Russian reason for including this source range."},
        "focus": {
            "type": "array",
            "description": (
                "Optional point-of-interest track for vertical (9:16) reframing of a wider source. "
                "List the horizontal centre of the main subject/action over time so the vertical crop "
                "can follow it. Sample roughly one point every 2-4 seconds (and at any hard subject "
                "change), keeping motion gradual. Omit entirely if the framing should stay centred."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["t", "x"],
                "properties": {
                    "t": {"type": "number", "minimum": 0, "description": "Seconds from this segment's start."},
                    "x": {"type": "number", "minimum": 0, "maximum": 1, "description": "Horizontal centre of interest, 0=left … 1=right."},
                    "y": {"type": "number", "minimum": 0, "maximum": 1, "description": "Vertical centre of interest, 0=top … 1=bottom."},
                },
            },
        },
    },
}


ANALYSIS_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clips"],
    "properties": {
        "clips": {
            "type": "array",
            "description": (
                "Finished edit plans. For episodic fiction, clips[0] should be an "
                "Episode Story Recap with 4 to 6 ordered segments from the main plot. "
                "Additional clips may be standalone main story shorts. Group related "
                "source ranges into one clip by placing multiple items in that clip's "
                "segments array."
            ),
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
                    "title": {"type": "string", "minLength": 1, "maxLength": 100, "description": "Russian clip title."},
                    "description": {"type": "string", "maxLength": 500, "description": "Russian clip description."},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                    "category": {"type": "string", "maxLength": 40},
                    "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                    "segments": {
                        "type": "array",
                        "minItems": 1,
                        "description": (
                            "Ordered source ranges to concatenate into this one final clip. "
                            "Use multiple segments for setup, escalation, payoff, and consequence. "
                            "Do not return one long 2-3 minute range for a whole chapter."
                        ),
                        "items": SEGMENT_SCHEMA,
                    },
                },
            },
        },
    },
}
