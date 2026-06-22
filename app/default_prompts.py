from __future__ import annotations


DEFAULT_PROMPT_SEED_KEY = "default_prompt_presets_seeded"
DEFAULT_PROMPT_SEED_VERSION = "20260619-prompts-v20"

# Universal editing rules appended to EVERY analysis. These must be mode-neutral:
# no recap mandate and no "reject action unless it explains the plot" — that
# plot bias belongs only to recap mode and otherwise kills action/highlight clips.
UNIVERSAL_OUTPUT_RULES = """
Editing contract:
- A clip is the final video plan. A segment is only one source range inside that
  final clip.
- Do not use one clip per detected moment when nearby/related moments should be
  watched together.
- If two or more moments are part of the same scene, fight, argument, reveal,
  reaction chain, setup/payoff, joke, or montage idea, return ONE clips[] item
  containing multiple ordered segments[].
- Prefer fewer stronger multi-segment clips over many tiny one-segment clips.
  When the source has related beats, most clips should contain 2 to 5 segments.
  A one-segment clip is rare and only acceptable for a short continuous scene
  that already contains the hook, context, escalation, and payoff.
- Do not hide several beats inside one long continuous segment. If a scene has
  setup, escalation, reveal, reaction, and payoff, split those beats into short
  ordered segments inside the same clip.
- Each individual segment should usually be 12 to 35 seconds and must stay under
  75 seconds. If a scene is longer, split it into 2 to 4 ordered segments inside
  the same clip instead of returning one long source range.
- Do not split one continuous beat into separate clips just because there is a
  cutaway, pause, or subtitle gap. Keep the beat together as multiple segments
  inside the same clip.
- Do not create micro-segments. Each segment should be a complete watchable
  source range with full lines/actions and natural entry/exit points.
- Never cut a segment in the middle of a spoken sentence, reaction sound,
  subtitle line, or obvious character action. Start 1 to 2 seconds before it
  begins; end 1 to 2 seconds after the line, reaction, or action resolves.
- Returning several clips that each contain exactly one segment is usually a bad
  answer. Merge related ones.
- Before finalizing JSON, audit your answer: if several clips are related or
  close in time, merge them into one clip with multiple ordered segments.
- Write all clip titles, clip descriptions, segment titles, segment
  descriptions, and segment reasons in concise natural Russian.
""".strip()

# Appended ONLY when the prompt opts into recap mode (see prompt_wants_recap).
NARRATIVE_RECAP_RULES = """
Recap mode (this prompt asks for an episode recap):
- The FIRST clip must be an Episode Story Recap: a compact plot-essential edit
  that lets a viewer understand what happened in the episode. Additional clips
  may be standalone shorts.
- The recap is not a mechanical timeline dump. Include only the essential scenes:
  premise, inciting incident, key reveal/rule, protagonist choice or mistake,
  crisis/consequence, and new direction.
- clips[0] must have 4 to 6 ordered segments drawn from the main plot across the
  source, understandable without watching any other generated clip, finishing in
  roughly 90 to 150 seconds total.
- The recap must include one setup/inciting segment from the first third of the
  source and one consequence/new-direction segment from the final third.
- The first clip is not optional. If only one good clip is possible, make that
  clip the Episode Story Recap.
- Reject pretty visuals, reactions, gags, or action shots if they do not explain
  the plot, a character decision, a relationship change, or a consequence.
- Do not tile the episode into consecutive chapters just to cover the timeline.
  The recap should jump between essential scenes and skip connective tissue.
""".strip()

# Backwards-compatible combined block (still importable).
MULTI_SEGMENT_OUTPUT_RULES = f"{UNIVERSAL_OUTPUT_RULES}\n\n{NARRATIVE_RECAP_RULES}"


def prompt_wants_recap(prompt: str | None) -> bool:
    """Recap mode is opted into only by prompts that explicitly ask for the
    structured recap clip, so a casual mention of the word "recap" elsewhere does
    not flip an action/highlights prompt into recap mode."""

    text = str(prompt or "").lower()
    return "episode story recap" in text or "main plot recap" in text


def analysis_output_rules(prompt: str | None) -> str:
    """Universal editing rules, plus recap rules only when the prompt wants one."""

    if prompt_wants_recap(prompt):
        return f"{UNIVERSAL_OUTPUT_RULES}\n\n{NARRATIVE_RECAP_RULES}"
    return UNIVERSAL_OUTPUT_RULES


def analysis_mode_instructions(prompt: str | None) -> str:
    """One-line clip-shape instruction matching the prompt's mode."""

    if prompt_wants_recap(prompt):
        return (
            "For episodic fiction, clips[0] must be an Episode Story Recap with 4 to 6 ordered "
            "segments from the main plot, around 90 to 150 seconds total, so a viewer can understand "
            "what happened in the episode. Additional clips may be self-contained main story shorts "
            "around 45 to 105 seconds. Do not tile the episode into consecutive timeline slices; "
            "skip weak connective scenes. Do not return finished clips around 3 minutes. "
        )
    return (
        "Return 3 to 6 strong STANDALONE clips, each understandable on its own. Do not summarize the "
        "episode, do not make a recap, and do not walk the timeline in order. Prioritize the strongest "
        "moments the prompt asks for over plot-explaining filler; skip weak connective scenes. "
    )

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

FOCUS_TRACK_INSTRUCTION = (
    "REFRAMING FOCUS — for cropping a wide video to vertical 9:16, tell us where the main subject sits "
    "horizontally in each segment, as a \"focus\" array of {\"t\": seconds from the segment start, "
    "\"x\": 0=far left … 0.5=centre … 1=far right}. Think in terms of SHOTS, not motion:\n"
    "1. Default to ONE point per shot. If the subject stays on one side for the whole segment, return a "
    "SINGLE point (e.g. [{\"t\":0,\"x\":0.7}]) — that is the best, most common answer.\n"
    "2. Add another point ONLY at a real change: a hard cut to a new composition, or the subject clearly "
    "walking across the frame. Put the point at the moment of that change. Two people talking in one "
    "static shot = ONE point at the more important person, not switching back and forth.\n"
    "3. Give the ACTUAL position you see (continuous, e.g. 0.18 / 0.62 / 0.8). Do NOT alternate "
    "left-right between points and do NOT emit periodic 0.3/0.5/0.7 patterns — that is wrong.\n"
    "4. Do NOT lazily default to 0.5. Most action and dialogue shots are framed OFF-centre — if a "
    "face/person/action is clearly to one side, commit to that position (e.g. 0.68, 0.78). Use 0.5 only "
    "when the subject is genuinely centred; omit focus if the whole segment is centred."
)

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

ACTION_DRAMA_HIGHLIGHTS_PROMPT = """
This is a live-action ACTION series (Korean action drama / боевик). These clips
are for an ACTION channel. Your #1 job is to find the FIGHTS and the ACTION. If
this episode contains any fights or action, the result MUST be mostly action.
Never fill the result with quiet talking/drama scenes when fights are available —
that is a wrong answer.

Return 3 to 6 standalone clips[], best first. No recap, no episode summary, no
chronological walkthrough. Each clip must hook a viewer cold and pay off on its
own. A clip may contain one or more ordered segments[].

PRIORITY 1 — ACTION. Find ALL of these and take them first, as many as exist:
- Hand-to-hand fights, brawls, martial arts, one-vs-many beatdowns.
- Gun fights, shootouts, knife fights, weapon disarms and takedowns.
- Chases (foot or car), raids, ambushes, breaches, escapes.
- Heavy hits, knockouts, throws, finishing blows, stunts, crashes.
- A skilled character wrecking multiple opponents; a badass entrance or "he is
  built different" moment.
A fight is the product. Capture the WHOLE exchange: split a long fight into 2 to 5
ordered segments inside ONE clip (approach/trigger -> the clash -> the finisher),
not a single isolated hit and not one giant range.

PRIORITY 2 — only as strong standalone extras, or if there is genuinely not
enough action to fill the clips:
- A hard confrontation, threat, standoff, interrogation, a betrayal exposed, a
  villain reveal, a cold power move.
- A revenge beat, a shocking twist or cliffhanger, a memorable one-liner.
Use romance or plain dialogue ONLY when it is an undeniable top moment. Quiet
conversation is the last resort, never the default.

Rules:
- Pick action a viewer understands from the clip itself. If a hit needs a one-line
  setup, include that setup as an earlier segment in the SAME clip.
- Segments can be punchy: about 8 to 35 seconds each, never longer than 75. Keep
  each finished clip under about 90 seconds.
- Never cut mid-punch, mid-line, or mid-action. Start ~1 second before the action
  or line begins; end ~1 second after the hit, reaction, or line resolves.
  Include the full exchange rather than trimming through it.
- Boundary check before returning: if action or speech is already mid-way at the
  first frame, move start_sec earlier; if it is still going at the last frame,
  move end_sec later.
- Use only timestamps from the uploaded source. Never invent timestamps or exceed
  the uploaded duration.
- Skip intros, "previously on" recaps, openings/credits, dead air, and quiet
  connective scenes with no action or stakes.

Final audit before returning JSON: re-scan the whole episode specifically for
physical combat and action. If you ended up with mostly dialogue while the
episode has fights, that is wrong — drop the weak talking clips and replace them
with the fights you missed.

Write every clip title, clip description, segment title, segment description, and
segment reason in natural Russian. Titles must sell the specific action (the
fight, the takedown, the chase, the twist), not describe the whole episode.
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

PODCAST_QUOTES_PROMPT = """
Analyze this podcast, interview, or talking-head source for vertical short-form.
Find the most quotable, shareable spoken moments: a strong opinion, a surprising
admission, a punchy one-liner, a vivid story, an argument with a clear payoff, or
an emotional beat. Ignore filler, intros, sponsor reads, and rambling.

Return 3 to 6 clips[] as finished edit plans, best first. Each clip is one
publishable short and may contain one or more ordered segments[].

Rules:
- Each clip must work for someone who never heard the episode. If a quote needs a
  one-line setup (the question asked, the topic), include that as an earlier
  segment in the SAME clip.
- Prefer self-contained thoughts that start and end on complete sentences. Never
  cut in the middle of a spoken line.
- Prefer 15 to 60 seconds per clip; keep the strongest hook in the first 3
  seconds.
- Merge a setup question and its answer into one clip with two segments instead
  of two separate clips.
- Use only timestamps from the uploaded source.

Write every title, description, and reason in natural Russian. The title should
quote or tease the actual line, not summarize the episode.
""".strip()

EDUCATIONAL_PROMPT = """
Analyze this educational, tutorial, lecture, or explainer source for vertical
short-form. Pull out the highest-value standalone lessons: a single clear tip, a
counterintuitive fact, a concise how-to step, a myth being busted, or a "most
people get this wrong" moment.

Return 3 to 6 clips[] as finished edit plans, best first. Each clip teaches one
complete idea and may contain one or more ordered segments[].

Rules:
- Each clip must deliver one full, understandable takeaway on its own. Include the
  short setup (the question or problem) plus the answer in the same clip.
- Open on the hook or the promise of the payoff, not on throat-clearing.
- Prefer 20 to 75 seconds per clip. Never cut through a sentence or mid-step.
- Skip long tangents, repeated recaps, and "like and subscribe" asides.
- Use only timestamps from the uploaded source.

Write every title, description, and reason in natural Russian. The title should
state the concrete benefit or curiosity gap ("Почему...", "Как за 30 секунд...").
""".strip()

COMEDY_MOMENTS_PROMPT = """
Analyze this source for the funniest standalone moments for vertical short-form:
jokes with a clear setup and punchline, deadpan reactions, perfect comedic
timing, unexpected turns, bloopers, and running gags landing.

Return 3 to 6 clips[] as finished edit plans, best first. Each clip is one laugh
that works on its own and may contain one or more ordered segments[].

Rules:
- Always include the setup and the punchline (and the reaction, if it sells the
  joke) in the SAME clip as ordered segments. A punchline with no setup is weak.
- Start right before the setup; end right after the laugh/reaction resolves.
  Never cut through the punchline or a key reaction.
- Prefer 8 to 45 seconds per clip. Cut dead air and slow build-up that does not
  serve the joke.
- Use only timestamps from the uploaded source.

Write every title, description, and reason in natural Russian. The title should
tease the joke or reaction without fully spoiling the punchline.
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
        "task": "analysis",
        "label": "Action drama highlights",
        "prompt": ACTION_DRAMA_HIGHLIGHTS_PROMPT,
        "is_default": False,
    },
    {
        "task": "analysis",
        "label": "Подкаст: цитаты",
        "prompt": PODCAST_QUOTES_PROMPT,
        "is_default": False,
    },
    {
        "task": "analysis",
        "label": "Обучающее: тезисы",
        "prompt": EDUCATIONAL_PROMPT,
        "is_default": False,
    },
    {
        "task": "analysis",
        "label": "Юмор: смешные моменты",
        "prompt": COMEDY_MOMENTS_PROMPT,
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
