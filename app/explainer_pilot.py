"""Пилот объяснялок (клип 887, «3 ошибки сна»): маскот «Ух» и 18 поз, которые вошли в ролик.

Файлы — ``/data/runtime/explainer`` (на машине владельца ``D:\\vvf-data\\runtime\\explainer``):
``owl_sheet.jpg`` (лист), ``pose_*.jpg`` / ``p1x.jpg`` (позы 768×1376 на фоне #2A1B47).
Описания поз — те, по которым их рисовали (D:\\vvf-offload\\tmp\\aiv\\poses.sh, poses2.sh), сверены с
картинками; ``pose_3.jpg`` (вместо трёх пальцев показал два) в ролик не вошёл и не импортируется.
Импорт — ``app.explainer.import_pilot`` (MCP ``explainer_import_pilot``).
"""

from __future__ import annotations

SHEET_FILE = "owl_sheet.jpg"

MASCOT = {
    "slug": "uh",
    "name": "Ух",
    "identity_card": ("a chubby round midnight-blue owl with lavender belly, huge round yellow eyes, tiny orange "
                      "beak, head tufts, short wing-arms with three feather-fingers, oversized grey hoodie with a "
                      "tiny crescent-moon pin"),
    "style_passport": "Flat 2D vector cartoon, clean bold outlines, soft cel shading",
    "persona": ("Ух — сова-полуночник в худи, дружелюбный знаток сна и привычек. Говорит просто и коротко, "
                "обращается к зрителю на «ты», с лёгкой иронией, без занудства."),
    "bg_color": "#2a1b47",
    "accent_color": "#ffd447",
    "chip_color": "#7a6ff0",
    "outline_color": "#1b1030",
    # Голос совы — как в первых роликах: ElevenLabs через Polza, liam, ≈ 3 ₽ за ролик, каждый раз по карточке.
    # Владелец: «голос чтобы был единый на всех видео», Gemini ему не понравился.
    "voice": {"provider": "polza", "model": "elevenlabs/eleven-v4", "voice": "liam"},
    "music_track_id": 20,
    "captions_style": "hormozi",
    "notes": "пилот: клип 887 «3 ошибки сна»",
}

POSES = [
    {"file": "pose_01.jpg", "slug": "sleepy_pillow", "title": "зевает с подушкой", "emotion": "tired",
     "description": ("very sleepy and exhausted, yawning widely, heavy eyelids, dark circles under the eyes, hugging "
                     "a small pillow"),
     "tags": ["sleepy", "tired", "yawn", "pillow", "сонный", "устал", "зевает", "подушка", "разбит", "недосып"]},
    {"file": "pose_2.jpg", "slug": "point_viewer", "title": "указывает на зрителя", "emotion": "confident",
     "description": "confident smirk, leaning forward and pointing one wing-finger straight at the viewer",
     "tags": ["point", "viewer", "you", "confident", "указывает", "ты", "давай", "разберемся", "внимание", "уверенно"]},
    {"file": "p14.jpg", "slug": "three_fingers", "title": "показывает три", "emotion": "serious", "number": 3,
     "description": ("serious face, raising one wing high with exactly three feather-fingers spread apart (like "
                     "counting three), other wing on hip"),
     "tags": ["three", "count", "list", "number", "три", "считает", "список", "номер", "ошибки"]},
    {"file": "pose_4.jpg", "slug": "one_finger", "title": "показывает один", "emotion": "serious", "number": 1,
     "description": "serious face, holding up one wing showing one raised feather-finger",
     "tags": ["one", "first", "count", "number", "один", "первый", "номер", "пункт"]},
    {"file": "pose_5.jpg", "slug": "phone_in_bed", "title": "в кровати с телефоном", "emotion": "absorbed",
     "description": ("lying on its belly on a small cozy bed, chin on the pillow, eyes wide, face lit bright blue by "
                     "a glowing smartphone held in both wings"),
     "tags": ["phone", "smartphone", "bed", "night", "scrolling", "телефон", "кровать", "смартфон", "ночь",
              "листает"]},
    {"file": "pose_6.jpg", "slug": "shocked_screen", "title": "шок от яркого экрана", "emotion": "shocked",
     "description": ("shocked, eyes extremely wide and glowing, feathers bristled, a bright smartphone screen in "
                     "front of it"),
     "tags": ["shocked", "screen", "bright", "phone", "brain", "шок", "экран", "яркий", "мозг", "телефон"]},
    {"file": "pose_7.jpg", "slug": "awake_in_bed", "title": "не может уснуть", "emotion": "annoyed",
     "description": "sitting up in the small bed under a blanket, annoyed and wide awake, staring up, eyes bloodshot",
     "tags": ["insomnia", "awake", "bed", "annoyed", "бессонница", "не спит", "кровать", "откладывается", "уснуть"]},
    {"file": "pose_8.jpg", "slug": "phone_in_drawer", "title": "убирает телефон в ящик", "emotion": "determined",
     "description": ("determined face, firmly putting a smartphone into an open drawer of a small bedside table with "
                     "one wing"),
     "tags": ["put away", "drawer", "phone", "tip", "solution", "убери", "телефон", "ящик", "совет", "решение"]},
    {"file": "pose_9.jpg", "slug": "two_fingers", "title": "показывает два", "emotion": "serious", "number": 2,
     "description": "serious face, holding up one wing showing two raised feather-fingers",
     "tags": ["two", "second", "count", "number", "два", "второй", "номер", "пункт"]},
    {"file": "pose_0.jpg", "slug": "alarm_jump", "title": "подскочил от будильника", "emotion": "startled",
     "description": "startled, jumping up in the air from a small bed, a ringing red alarm clock next to it",
     "tags": ["alarm", "wake up", "morning", "startled", "будильник", "подъем", "утро", "вставать", "будни"]},
    {"file": "pose_1.jpg", "slug": "asleep_mask", "title": "сладко спит в маске", "emotion": "blissful",
     "description": ("blissfully asleep, curled up hugging a pillow, wearing a sleep mask pushed up on the forehead, "
                     "little drool"),
     "tags": ["asleep", "sleep", "mask", "weekend", "oversleep", "спит", "сон", "маска", "выходные", "отсыпается"]},
    {"file": "p12.jpg", "slug": "jetlag_suitcase", "title": "сбит с толку, чемодан", "emotion": "dizzy",
     "description": "dizzy and confused, spiral eyes, wobbling, holding a small travel suitcase",
     "tags": ["dizzy", "confused", "suitcase", "travel", "jetlag", "кружится", "чемодан", "перелет", "сбит",
              "организм"]},
    {"file": "pose_13.jpg", "slug": "stretch_fresh", "title": "бодро потягивается", "emotion": "happy",
     "description": "cheerfully stretching both wings up, fresh and energetic, happy closed-eye smile",
     "tags": ["energetic", "fresh", "stretch", "morning", "happy", "бодрый", "утро", "вставай", "энергия",
              "каждый день"]},
    {"file": "pose_15.jpg", "slug": "coffee_mug", "title": "пьёт кофе", "emotion": "happy",
     "description": "happily sipping from a huge steaming coffee mug held in both wings, eyes wide",
     "tags": ["coffee", "mug", "drink", "кофе", "кружка", "пьет", "обед", "кофеин"]},
    {"file": "p16.jpg", "slug": "over_caffeinated", "title": "перебрал кофеина", "emotion": "jittery",
     "description": ("jittery and over-caffeinated, eyes twitching, feathers sticking out, clutching an empty latte "
                     "cup, a little crescent moon floating above"),
     "tags": ["jittery", "caffeine", "latte", "night", "awake", "кофеин", "латте", "полночь", "бодрит", "дергается"]},
    {"file": "pose_17.jpg", "slug": "clock_teacher", "title": "учитель у часов", "emotion": "wise",
     "description": ("teacher pose, pointing with one wing at a round wall clock that shows two o'clock, other wing "
                     "raised wisely"),
     "tags": ["clock", "time", "rule", "deadline", "teacher", "часы", "время", "правило", "до двух", "последняя"]},
    {"file": "p18.jpg", "slug": "thumbs_up", "title": "палец вверх", "emotion": "happy",
     "description": "big happy smile, giving a thumbs-up with one wing, sparkle in the eyes",
     "tags": ["thumbs up", "good", "result", "approve", "happy", "класс", "отлично", "исправь", "разница",
              "результат"]},
    {"file": "pose_19.jpg", "slug": "wave_subscribe", "title": "машет: подпишись", "emotion": "cheerful",
     "description": "cheerfully waving goodbye with one wing and pointing up with the other, big smile",
     "tags": ["wave", "goodbye", "subscribe", "bye", "cta", "пока", "подпишись", "подписка", "машет", "следующее"]},
]
