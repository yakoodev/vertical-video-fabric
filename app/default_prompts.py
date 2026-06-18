from __future__ import annotations


DEFAULT_PROMPT_SEED_KEY = "default_prompt_presets_seeded"
DEFAULT_PROMPT_SEED_VERSION = "20260617-prompts-v17"

MULTI_SEGMENT_OUTPUT_RULES = """
Mandatory editing contract:
- A clip is the final video plan. A segment is only one source range inside that
  final clip.
- For anime, TV series, or other episodic fiction, the FIRST clip must be an
  Episode Story Recap: a compact plot-essential edit that lets a viewer
  understand what happened in the episode. Additional clips may be standalone
  main story shorts.
- The recap clip is not a mechanical timeline dump. It must include only the
  essential scenes: premise, inciting incident, key reveal/rule, protagonist
  choice or mistake, crisis/consequence, and new direction.
- Do not use one clip per detected moment when nearby/related moments should be
  watched together.
- If two or more moments are part of the same scene, joke, fight, argument,
  reveal, reaction chain, setup/payoff, or montage idea, return ONE clips[] item
  containing multiple ordered segments[].
- Prefer fewer stronger multi-segment clips over many tiny one-segment clips.
- When the source has related beats, most clips should contain 2 to 5 segments.
  A one-segment clip is rare and only acceptable for a short continuous scene
  that already contains the hook, context, escalation, and payoff.
- Do not hide several beats inside one long continuous segment. If a scene has
  setup, escalation, reveal, reaction, and payoff, split those beats into short
  ordered segments inside the same clip.
- For anime/series/episodic fiction, each individual segment should usually be
  12 to 35 seconds and must stay under 75 seconds. If a scene is longer, split
  it into 2 to 4 ordered segments inside the same clip instead of returning one
  long source range.
- For fiction sources, do not split one plot beat into separate clips just
  because there is a cutaway, pause, or subtitle gap. Keep the beat together as
  multiple segments inside the same clip.
- Do not create micro-segments. Each segment should be a complete watchable
  source range with full spoken lines and natural entry/exit points.
- Never cut a segment in the middle of a spoken sentence, reaction sound,
  subtitle line, or obvious character action. Start 1 to 2 seconds before the
  first complete line begins; end 1 to 2 seconds after the line, reaction, or
  action resolves.
- For an episodic/anime/series source, at least two returned clips should use
  multiple segments when the episode has enough related material.
- For a long episodic/anime/series source, clips[0] must be the Episode Story
  Recap with 4 to 6 ordered segments drawn from the main plot across the source.
  It should be understandable without watching any other generated clip and
  should finish in roughly 90 to 150 seconds total. Do not make a 3-minute recap
  unless the actual plot cannot be understood without it.
- The recap must include one setup/inciting segment from the first third of the
  uploaded source and one consequence/new-direction segment from the final
  third. If you cannot find those, return the closest plot-bearing scene in
  those thirds rather than starting the recap in the middle of the episode.
- The first clip is not optional for episodic fiction. If only one good clip is
  possible, make that clip the Episode Story Recap and skip optional shorts.
- Returning 4 or 5 clips that each contain exactly one segment is usually a bad
  answer. Merge them unless they are truly unrelated moments.
- Reject pretty visuals, reactions, gags, or action shots if they do not explain
  the plot, a character decision, a relationship change, or a consequence.
- Do not tile the episode into consecutive chapters just to cover the timeline.
  The recap should jump between essential scenes and skip connective tissue.
  If the result looks like "every next scene in order", it is the wrong answer.
- Before finalizing JSON, audit your answer: if several clips have only one
  segment and are related or close in time, merge them into one clip with
  multiple segments.
- Write all clip titles, clip descriptions, segment titles, segment
  descriptions, and segment reasons in Russian. Use concise natural Russian
  phrasing suitable for a Russian-speaking editor.
""".strip()

LEGACY_DEFAULT_PROMPTS = {
    (
        "analysis",
        "Apex analysis",
    ): """
Find strong vertical short-form moments in the source video.
The default content context is Apex Legends gameplay: expect squad voice chat,
legend names, weapons, damage numbers, knocks, revives, rotations, third parties,
ranked callouts, and fast fight pacing.
Return clips that work standalone, have a clear hook, and contain conflict,
emotion, humor, insight, tension, or spectacle. Prefer moments between 5 and
180 seconds and include a practical title and description for publishing.
When the source has enough material, return 3 to 5 distinct moments so they can
be stitched into a montage.
""".strip(),
    (
        "analysis",
        "Anime analysis",
    ): """
Analyze this anime source for vertical short-form clips.
Look for moments that work without long setup: expressive reactions, jokes,
romance tension, character conflict, emotional reveals, fight beats, power
demonstrations, visual spectacle, cliffhangers, or memorable dialogue.
Prefer scenes where the viewer can understand the hook from the clip itself.
Keep spoilers practical: avoid selecting a late reveal unless the moment is
clearly the strongest standalone short.
Return 3 to 5 distinct clips when enough material exists. Prefer 5 to 180
second ranges, and include titles/descriptions that name the moment, character
dynamic, emotion, or conflict instead of generic episode summaries.
""".strip(),
    (
        "analysis",
        "Series analysis",
    ): """
Analyze this TV series or episodic live-action source for vertical short-form
clips.
Find scenes that can stand alone for Shorts/TikTok: sharp dialogue, conflict,
comedy, tension, betrayal, investigation payoff, emotional confession, romantic
beat, plot twist, cliffhanger, or a clear character decision.
Avoid clips that require too much previous context. Prefer moments with a clear
beginning, escalation, and payoff inside the selected time range.
Return 3 to 5 distinct clips when enough material exists. Prefer 5 to 180
second ranges, and write concise titles/descriptions that explain the hook and
why the scene is worth watching.
""".strip(),
    (
        "publishing",
        "Publishing metadata",
    ): """
Generate publishing metadata for a rendered vertical clip.
Return JSON only with title and description suitable for YouTube Shorts and TikTok.
Keep the title short, direct, and based on the clip content.
""".strip(),
    (
        "subtitle",
        "Apex subtitles",
    ): """
Transcribe this audio for karaoke subtitles.
Return JSON only with segment-level and word-level timestamps.
Context: this is Apex Legends gameplay. Preserve likely game terms, legend names,
weapon names, damage/knock callouts, pings, rotations, and squad communication
instead of rewriting them as generic speech.
""".strip(),
}

BASE_ANALYSIS_PROMPT = """
Analyze this source for vertical short-form publishing.

Return clips[] as finished edit plans, not just isolated detections. Each clip
may contain one or more non-overlapping segments[]. Use multiple segments inside
one clip when the moments belong to the same story, joke, fight, argument,
setup/payoff, reaction chain, comparison, or montage theme. Do not split those
related beats into separate clips unless they only work independently.

Hard requirement: if there are several related highlights close together, merge
them into one clips[] item with multiple segments[]. A one-segment clip is only
acceptable when that single continuous range is already the whole final video.

Selection rules:
- Build 3 to 5 finished clips when the source has enough material.
- Prefer 1 to 3 stronger multi-segment clips over 5 weak isolated cuts when the
  source is one episode, match, or continuous scene.
- Prefer clips that have a fast hook, clear context, escalation, and payoff.
- Prefer 5 to 180 seconds total per clip after its segments are combined.
- If a clip needs context, include a short setup segment before the payoff.
- Do not solve context by making one huge continuous range. Use several tighter
  segments in the same clip when the source has distinct beats.
- Do not create tiny fragments. Each segment should start and end on natural
  speech/action boundaries and include complete voice lines.
- If several short highlights are similar, combine the best 2 to 5 into one
  montage-style clip with a single title and reason.
- Avoid dead air, menus, loading screens, repeated exposition, and weak endings.

Default content context is Apex Legends gameplay. Preserve tactical meaning:
legend names, weapons, damage, knocks, revives, rotations, third parties,
ranked callouts, clutch attempts, squad communication, and fight pacing.

For each clip, write a Russian title and Russian description for the combined
final video. For each segment, explain in Russian why that exact range belongs
in that clip.
""".strip()

ANIME_ANALYSIS_PROMPT = """
Analyze this anime episode and pick the strongest standalone moments for vertical
short-form (Shorts/TikTok). Do NOT summarize the episode and do NOT walk the
timeline in order. Each returned clip must hook a viewer who has never seen the
show and pay off on its own.

Return 3 to 5 clips[] as finished edit plans, ordered best first. Each clip is
one publishable short; a clip may contain one or more ordered segments[].

What makes a strong moment (pick these):
- A big emotional peak: confession, heartbreak, sacrifice, reunion, a character
  breaking down or finally standing up.
- A funny beat: a joke with setup and punchline, a deadpan reaction, a running
  gag landing.
- Romance/tension: a charged exchange, a near-kiss, jealousy, a bold line.
- An epic action or fight beat: a turning point in a fight, a finishing blow, a
  transformation or power reveal, a clutch save.
- A shocking twist, betrayal, cliffhanger, or "no way" reveal.
- A memorable, quotable line delivered with weight.

Rules:
- Prefer moments a viewer understands from the clip itself with little context.
  If a punchline or reveal needs a quick setup, include that setup as an earlier
  segment in the SAME clip instead of a separate clip.
- Use multiple segments inside one clip only when the shots truly belong to the
  same moment (setup -> payoff, joke -> reaction, attack -> impact). Otherwise
  keep separate moments as separate clips.
- Each segment should be about 12 to 35 seconds and never longer than 75. If a
  scene runs long, split it into 2 to 4 ordered segments in the same clip. Keep
  each finished clip under about 120 seconds total.
- Never cut in the middle of a spoken line, subtitle, breath, reaction sound, a
  transformation shot, or an obvious action. Start about 1 second before the
  first complete line or just before the action begins; end about 1 second after
  the line, reaction, or action fully resolves. If unsure, include the whole
  phrase rather than trimming through it.
- Boundary check before returning: look at the first and last second of every
  segment. If speech or action is already mid-way at the first frame, move
  start_sec earlier; if it is still going at the last frame, move end_sec later.
- Use only timestamps from the uploaded source. Never invent timestamps from
  memory or use ranges beyond the uploaded duration.
- Skip intros, openings/endings (OP/ED), credits, "previously on" segments, dead
  air, slow connective scenes, and pretty-but-empty shots that carry no emotion,
  joke, or stakes.

Write every clip title, clip description, segment title, segment description, and
segment reason in natural Russian. The title should sell the specific moment
(the emotion, joke, or reveal), not describe the episode.
""".strip()

SERIES_ANALYSIS_PROMPT = """
Analyze this TV series or episodic live-action source for vertical publishing.
The first returned clip must be an Episode Story Recap: a compact edit of the
most important scenes that lets a viewer understand the main plot of the
uploaded episode. The goal is not random highlights and not a mechanical full
timeline dump.

Return clips[] as finished edit plans. Each clip may contain one or more
segments[].

clips[0] is mandatory and must be titled like "Episode Story Recap" or "Main
Plot Recap". It must contain 4 to 6 ordered segments from the actual main plot:
the protagonist's initial situation/goal, the inciting incident, the key
secret/rule reveal, the protagonist's choice or mistake, the crisis or disaster,
the consequence, and the new direction/cliffhanger. This recap clip may jump
across the episode timeline. It should let a viewer understand what happened in
the episode without watching any other generated clip. It must feel like a
short connected digest, not like several random moments.

Additional clips, if returned, are self-contained mini-arcs: an investigation
step, accusation, choice, betrayal, confession, consequence, relationship shift,
comedic setup and payoff, argument escalation, reveal, or cliffhanger.

Hard requirement: do not output separate one-segment clips for setup, response,
payoff, and aftermath when they are part of the same plot beat. Merge them into
one clips[] item with multiple ordered segments[].

Selection rules:
- Build 1 to 3 finished clips when the uploaded source is longer than 15
  minutes: one required recap clip first, then 0 to 2 optional standalone story
  clips. It is better to return one strong recap than several weak long clips.
- The recap clip must select the strongest main plot essentials, not every beat
  in order. Skip weak connective scenes, repeated setup, and scenes that do not
  change the viewer's understanding of the plot.
- The recap must include an early setup/inciting segment from the first third of
  the uploaded episode and a late consequence/new-direction segment from the
  final third. A recap that starts only after the story is already underway is
  invalid.
- If the episode spends time on atmosphere before the plot starts, still include
  the earliest plot-bearing scene from the first third: goal, clue, relationship
  tension, unusual event, or inciting problem.
- The recap is mandatory. If there is only enough material for one good returned
  clip, return only the recap and do not add weaker optional clips.
- Prefer one strong recap clip plus at most a few strong standalone clips over
  many chronological slices.
- Choose plot-bearing beats first: inciting incident, clue, confrontation,
  character choice, confession, betrayal, emotional turn, consequence,
  reversal, or cliffhanger.
- Keep segments inside each clip in source-time order. The recap clip may jump
  across large gaps in the episode when those jumps connect essential plot
  beats.
- Every returned clip must answer: what happened, why it matters, and what
  changed after it.
- The recap clip must have a clear through-line from setup to consequence. Other
  clips must have their own local hook, context, escalation/reveal, and payoff.
- Use multiple segments inside a clip to connect setup -> escalation -> payoff.
  A one-segment clip is acceptable only when that continuous range is 25 to 60
  seconds and already contains the whole mini-arc with complete context and
  resolution.
- Do not hide several beats inside one long continuous segment. If a scene has
  setup, response, reveal, consequence, and aftermath, split those beats into
  short ordered segments inside the same clip.
- Prefer recap clip length around 90 to 150 seconds using 4 to 6 short
  segments. Prefer additional clip length around 45 to 105 seconds. Keep each
  individual segment around 12 to 35 seconds and never longer than 75 seconds.
  If a dialogue exchange or action sequence is longer, split it into 2 to 4
  ordered segments inside the same clip.
- Do not return any finished clip around 3 minutes. If your chosen clip is that
  long, remove weaker connective material or split the idea into a shorter
  self-contained arc.
- Use only timestamps from the uploaded source. Never use timestamps from
  memory, another cut, a full episode, or any range after the uploaded source
  duration.
- Segment boundaries must be editable and subtitle-safe: never cut in the
  middle of dialogue, subtitle line, breath, reaction sound, or obvious
  character action.
- Start 1 to 2 seconds before a complete spoken line begins, or on a clear shot
  change before the action starts. End 1 to 2 seconds after the spoken line,
  reaction, or action resolves. If uncertain, include context around the whole
  line instead of trimming through the phrase.
- Boundary audit: before returning JSON, inspect the first and last second of
  every segment. If speech/action is already in progress at the first frame,
  move start_sec earlier. If speech/action is still in progress at the last
  frame, move end_sec later or split the beat into another segment in the same
  clip.
- If two lines are separated by dead air, keep the useful setup and payoff as
  separate ordered segments inside the same clip; do not include the entire
  silent gap as one long segment.
- Avoid isolated reactions, pretty shots, jokes, or action beats unless they
  explain the plot, a character decision, relationship change, consequence, or
  cliffhanger.
- Avoid credits, dead air, filler dialogue, repeated exposition, and scenes that
  do not help the viewer follow the episode's story.

Titles/descriptions must be written in Russian and should describe the recap or
self-contained plot arc and its consequence, not just the hook of an isolated
moment. Segment titles, descriptions, and reasons must also be in Russian and
explain why that source range is necessary for this clip.
""".strip()

DEFAULT_PUBLISHING_PROMPT = """
Generate publishing metadata for a rendered vertical clip.
Return JSON only with title and description suitable for YouTube Shorts and TikTok.
Both title and description must be in Russian.

Title rules:
- Make the title specific to the clip, not generic.
- Lead with the hook, conflict, emotion, or payoff.
- Keep it short enough to scan quickly.
- Do not invent facts that are not visible/audible in the clip.
- Use natural Russian, not literal English-style phrasing.

Description rules:
- Summarize the moment in one or two compact sentences.
- Mention the key context if the clip is a montage or has multiple segments.
- Avoid clickbait that misrepresents the scene.
- Use Russian punctuation and compact wording.
""".strip()

DEFAULT_SUBTITLE_PROMPT = """
Transcribe this audio for karaoke subtitles.
Return JSON only with segment-level and word-level timestamps.

Rules:
- Preserve exact wording when possible; do not rewrite speech into polished text.
- Keep filler, interruptions, laughs, callouts, and short reactions if audible.
- Split readable subtitle segments by phrase, speaker turn, or natural pause.
- Keep word timestamps aligned tightly enough for karaoke highlighting.
- Do not anticipate speech. If uncertain, set a word start slightly after the
  audible word begins rather than before it.
- Do not stretch a word or segment through silence. If a phrase is followed by
  a pause and then another phrase, end the previous word/segment at the phrase
  end and start a new segment for the next phrase.
- Word end timestamps should be close to the audible end of the word, not the
  start of the next word after a pause.
- Use punctuation lightly for readability, but do not change meaning.

Context: Apex Legends gameplay. Preserve likely game terms, legend names,
weapon names, damage/knock callouts, pings, rotations, third parties, ranked
callouts, and squad communication instead of replacing them with generic words.
""".strip()

ANIME_SUBTITLE_PROMPT = """
Transcribe this anime audio for karaoke subtitles.
Return JSON only with segment-level and word-level timestamps.

Rules:
- Preserve character names, honorifics, attack names, locations, and recurring
  terms when they are audible.
- Keep emotional interjections, gasps, laughs, screams, and short reactions when
  they matter to timing or meaning.
- Do not summarize or localize aggressively; transcribe what is said.
- Split subtitle segments by phrase, speaker turn, or natural pause.
- Keep word timestamps tight for karaoke highlighting.
- Do not anticipate speech. If uncertain, set a word start slightly after the
  audible syllable begins rather than before it.
- Do not stretch subtitles across dramatic silence, reaction pauses, breath
  pauses, or shot holds. A phrase, then pause, then phrase must become separate
  timed subtitle segments with no text displayed during the pause.
- Word end timestamps should be close to the audible end of each word/syllable,
  not extended until the next spoken line.
- If there are multiple languages, detect them and keep names/terms intact.
""".strip()

SERIES_SUBTITLE_PROMPT = """
Transcribe this TV series audio for karaoke subtitles.
Return JSON only with segment-level and word-level timestamps.

Rules:
- Preserve exact dialogue, names, places, slang, whispered lines, and meaningful
  interruptions when audible.
- Do not paraphrase dramatic or comedic timing; keep the spoken rhythm.
- Split subtitle segments by speaker turn, phrase, or natural pause.
- Keep word timestamps tight for karaoke highlighting.
- Do not anticipate speech. If uncertain, set a word start slightly after the
  audible word begins rather than before it.
- Do not stretch subtitles across dramatic silence, reaction pauses, breath
  pauses, or shot holds. A phrase, then pause, then phrase must become separate
  timed subtitle segments with no text displayed during the pause.
- Word end timestamps should be close to the audible end of each word, not
  extended until the next spoken line.
- Use punctuation for readability, but do not add interpretation that is not in
  the audio.
- If background speech overlaps with primary dialogue, prioritize the clearest
  foreground speaker.
""".strip()

DEFAULT_PROMPT_PRESETS = (
    {
        "task": "analysis",
        "label": "Apex analysis",
        "prompt": BASE_ANALYSIS_PROMPT,
        "is_default": True,
    },
    {
        "task": "analysis",
        "label": "Anime analysis",
        "prompt": ANIME_ANALYSIS_PROMPT,
        "is_default": False,
    },
    {
        "task": "analysis",
        "label": "Series analysis",
        "prompt": SERIES_ANALYSIS_PROMPT,
        "is_default": False,
    },
    {
        "task": "publishing",
        "label": "Publishing metadata",
        "prompt": DEFAULT_PUBLISHING_PROMPT,
        "is_default": True,
    },
    {
        "task": "subtitle",
        "label": "Apex subtitles",
        "prompt": DEFAULT_SUBTITLE_PROMPT,
        "is_default": True,
    },
    {
        "task": "subtitle",
        "label": "Anime subtitles",
        "prompt": ANIME_SUBTITLE_PROMPT,
        "is_default": False,
    },
    {
        "task": "subtitle",
        "label": "Series subtitles",
        "prompt": SERIES_SUBTITLE_PROMPT,
        "is_default": False,
    },
)
