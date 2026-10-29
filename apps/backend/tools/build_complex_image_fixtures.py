"""Reproduce authored benchmark sheets; licensed camera c01/c02 must exist first.

Gold is authored before provider observations. No network or provider calls.
"""

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

root = Path(__file__).resolve().parents[1] / "tests/fixtures/ai_analysis"
manifest = json.loads((root / "image-complex-manifest.json").read_text())
manifest = {"version": 1, "images": manifest["images"][:2]}
cases = []
font = "/System/Library/Fonts/Supplemental/Arial.ttf"


def page(lines, size=34):
    im = Image.new("RGB", (1400, 1000), "white")
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(font, size)
    for i, line in enumerate(lines):
        d.text((65, 65 + i * (size + 26)), line, font=f, fill=(24, 29, 34))
    return im


def save(ident, im, origin, changes="none"):
    p = root / "complex-images" / f"{ident}.jpg"
    im.save(p, quality=94)
    manifest["images"].append(
        {
            "id": ident,
            "file": f"complex-images/{ident}.jpg",
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "width": im.width,
            "height": im.height,
            "origin": origin,
            "changes": changes,
            "author": "StudGroup test fixture author",
            "license": "repository test fixture",
        }
    )
    return ident


def case(
    ident,
    images,
    facts,
    categories,
    *,
    messages=None,
    actions=None,
    audience="unknown",
    dates=None,
    numbers=None,
    cards=None,
    display=None,
    notes="",
):
    if cards is None:
        cards = []
    if numbers is None:
        numbers = []
    if dates is None:
        dates = []
    if actions is None:
        actions = []
    if messages is None:
        messages = []
    cases.append(
        {
            "id": ident,
            "images": images,
            "messages": messages,
            "gold": {
                "facts": facts,
                "categories": categories,
                "actions": actions,
                "audience": audience,
                "dates": dates,
                "numbers": numbers,
                "cards": cards,
                "display": images if display is None else display,
                "notes": notes,
            },
        }
    )


case(
    "camera-notebook",
    ["c01"],
    [
        "Handwritten multivariable calculus on lined paper",
        "Partial derivatives and a limit at (0,0)",
        "Text at edges is cropped and some handwriting is uncertain",
    ],
    ["material", "unknown"],
    numbers=["0"],
    display=["c01"],
    notes="Camera photograph; independently inspected before any provider call. Do not invent a homework instruction, deadline or a complete transcription of cut-off fragments.",
)
case(
    "camera-blackboard",
    ["c02"],
    ["E = mc², chalk on a green board"],
    ["material"],
    numbers=["2"],
    display=["c02"],
)
save(
    "c03",
    Image.open(root / "complex-images/c01.jpg").rotate(90, expand=True),
    "derivative of licensed c01; same author and CC BY-SA 3.0",
    "rotated 90 degrees; camera perspective retained",
)
case(
    "rotated-handwriting",
    ["c03"],
    ["Handwritten calculus, same content as c01", "No assignment instruction"],
    ["material", "unknown"],
    numbers=["0"],
)
board = Image.open(root / "complex-images/c02.jpg").resize((1500, 1001))
save(
    "c04",
    ImageEnhance.Brightness(board).enhance(0.40),
    "derivative of licensed c02; same author and CC BY-SA 4.0",
    "darkened to 40 percent, resized",
)
case("dark-board", ["c04"], ["E = mc²"], ["material"], numbers=["2"])
save(
    "c05",
    board.filter(ImageFilter.GaussianBlur(9)),
    "derivative of licensed c02; same author and CC BY-SA 4.0",
    "blur radius9, resized",
)
case(
    "blurred-board",
    ["c05"],
    ["Formula is blurred; avoid invented surrounding text"],
    ["material", "unknown"],
    notes="Recognizing E=mc² is allowed if supported; uncertainty is preferred to invented details.",
)
lines = [
    "БИ 1.2 · расписание с 02.11.2026 по 29.11.2026",
    "Понедельник, первая неделя",
    "09:00–10:30  Алгебра  ауд. 205  Иванова И.И.",
    "Подгруппа 1",
    "Понедельник, вторая неделя",
    "10:40–12:10  Физика  ауд. 310  Петров П.П.",
    "Подгруппа 2",
]
save("c06", page(lines, 32), "authored printed timetable")
case(
    "parity-subgroups",
    ["c06"],
    lines,
    ["schedule"],
    audience="subgroup",
    dates=["2026-11-02", "2026-11-29"],
    numbers=["205", "310", "1", "2"],
    notes="Two distinct rows; first/second week and subgroup labels must remain separate. No homework cards.",
)
change = [
    "Изменение расписания БИ 1.2",
    "Только 05.11.2026, четверг, подгруппа 2",
    "Физика: вместо 09:00–10:30 — 13:00–14:30",
    "Новая аудитория: 412",
    "Преподаватель: Петров П.П.",
]
im = page(change)
im = im.transform(
    im.size, Image.Transform.AFFINE, (1, 0.11, -60, 0.05, 1, -35), fillcolor=(160, 150, 130)
)
save(
    "c07",
    im,
    "authored sheet with simulated camera geometry",
    "affine skew and brown exposed border, no real camera claim",
)
case(
    "skewed-once-change",
    ["c07"],
    change,
    ["schedule_change"],
    audience="subgroup",
    dates=["2026-11-05"],
    numbers=["2", "412"],
    notes="One-time change only; no recurring pattern and no auto publication.",
)
hw = [
    "Алгебра · БИ 1.2",
    "К 06.11.2026 решить задачи 17–19.",
    "17) x² − 5x + 6 = 0",
    "18) 2x + 7 = 15",
    "19) Найти производную f(x) = x³.",
    "Решения загрузить в LMS до 18:00.",
]
save(
    "c08",
    ImageEnhance.Brightness(page(hw)).enhance(0.55),
    "authored worksheet; simulated dim light",
    "brightness55percent",
)
card = {
    "kind": "homework",
    "subject": "Алгебра",
    "required_terms": ["17", "19", "LMS"],
    "forbidden_terms": [],
    "deadline": "2026-11-06T18:00:00+03:00",
}
case(
    "dim-homework",
    ["c08"],
    hw,
    ["homework"],
    actions=["solve17-19", "uploadLMS"],
    audience="group",
    dates=["2026-11-06T18:00:00+03:00"],
    numbers=["17", "18", "19", "5", "6", "2", "7", "15", "3"],
    cards=[card],
)
small = [
    "Теория вероятностей",
    "Для всей группы БИ 1.2: к 09.11.2026 подготовить решения.",
    "Задачи № 21, 23 и 28. В задаче 23 вероятность p = 0,025.",
    "Не округлять промежуточные вычисления. Отправить PDF преподавателю.",
]
save("c09", page(small, 19), "authored small-print exercise sheet")
case(
    "small-numbers",
    ["c09"],
    small,
    ["homework"],
    actions=["solve21,23,28", "sendPDF"],
    audience="group",
    dates=["2026-11-09"],
    numbers=["21", "23", "28", "0.025"],
    cards=[
        {
            "kind": "homework",
            "subject": "Теория вероятностей",
            "required_terms": ["21", "23", "28", "PDF"],
            "forbidden_terms": ["0.25"],
            "deadline": "2026-11-09",
        }
    ],
)
classwork = [
    "Алгебра · работа на занятии 29.10.2026",
    "В классе: задачи 1–4. Домой их не задавали.",
    "Образец: x² − 1 = (x − 1)(x + 1).",
]
save("c10", page(classwork), "authored in-class sheet")
case(
    "classwork-negative",
    ["c10"],
    classwork,
    ["in_class_task"],
    dates=["2026-10-29"],
    numbers=["1", "4"],
    notes="Must not become homework even though mathematical exercises are present.",
)
material = [
    "Физика · конспект лекции",
    "Закон Ома: I = U / R",
    "I — сила тока, U — напряжение, R — сопротивление.",
]
assignment = [
    "Физика · БИ 1.2",
    "К 10.11.2026: решить № 31 и 32 из практикума.",
    "Использовать закон Ома. Принести решения на занятие.",
]
save("c11a", page(material), "authored lecture notes")
save("c11b", page(assignment), "authored assignment sheet")
case(
    "material-plus-homework",
    ["c11a", "c11b"],
    material + assignment,
    ["material", "homework"],
    actions=["solve31,32", "bringSolutions"],
    audience="group",
    dates=["2026-11-10"],
    numbers=["31", "32"],
    cards=[
        {
            "kind": "homework",
            "subject": "Физика",
            "required_terms": ["31", "32"],
            "forbidden_terms": [],
            "deadline": "2026-11-10",
        }
    ],
    display=["c11b"],
    notes="Lecture note is context; assignment is on second image. Display reduction requires verified coverage, otherwise retain extra material conservatively.",
)
chat1 = [
    "29 октября 2026",
    "Преподаватель, 10:00",
    "БИ 1.2, по истории к 12 ноября подготовить доклад.",
    "Тема: реформы Петра I. Объём 4 страницы.",
]
chat2 = [
    "Преподаватель, 10:05",
    "Уточнение к докладу по истории: объём 6 страниц, не 4.",
    "Сдать в LMS до 12.11.2026, 16:30.",
]
save("c12a", page(chat1), "authored chat screenshot mock; no real users")
save("c12b", page(chat2), "authored chat screenshot mock; no real users")
case(
    "screenshots-correction",
    ["c12a", "c12b"],
    chat1 + chat2,
    ["homework"],
    actions=["report6pages", "uploadLMS"],
    audience="group",
    dates=["2026-11-12T16:30:00+03:00"],
    numbers=["6", "16:30"],
    cards=[
        {
            "kind": "homework",
            "subject": "История",
            "required_terms": ["Петр", "6", "LMS"],
            "forbidden_terms": ["4 страницы"],
            "deadline": "2026-11-12T16:30:00+03:00",
        }
    ],
    notes="Second screenshot explicitly overrides the first. One card, not two.",
)
poster = [
    "Открытая лекция «За пределами Земли»",
    "Для студентов БИ 1.2",
    "15 ноября 2026, 17:30–19:00",
    "Место: корпус Б, зал 204",
    "Участие бесплатное. Регистрация до 13 ноября.",
    "С собой студенческий билет.",
]
save("c13", page(poster), "authored group event poster")
case(
    "group-poster",
    ["c13"],
    poster,
    ["event"],
    actions=["editorReview"],
    audience="group",
    dates=["2026-11-15T17:30:00+03:00", "2026-11-15T19:00:00+03:00", "2026-11-13"],
    numbers=["204"],
    cards=[],
    notes="Event draft only; never homework or automatic student publication.",
)
personal = [
    "Именное приглашение",
    "Только для Марии Соколовой",
    "Индивидуальная консультация 16.11.2026 в 12:00",
    "Кабинет 104. Приглашение не передавать.",
]
save("c14", page(personal), "authored fictional personal invitation")
case(
    "personal-poster",
    ["c14"],
    personal,
    ["event", "organizational"],
    audience="personal",
    dates=["2026-11-16T12:00:00+03:00"],
    numbers=["104"],
    cards=[],
    notes="Fictional name; must never become group event publication.",
)
# Aligned source strips: first and second overlap; third covers both. Same pixels, no category metadata.
base = page(
    [
        "Алгебра · БИ 1.2",
        "К 18.11.2026 решить задачи 41–44.",
        "41) 3x + 2 = 11",
        "42) x² = 49",
        "43) 5x − 4 = 21",
        "44) x³ = 125",
    ]
)
for ident, box in [
    ("c15a", (0, 0, 1400, 240)),
    ("c15b", (0, 180, 1400, 650)),
    ("c15c", (0, 0, 1400, 650)),
]:
    save(ident, base.crop(box), "authored overlapping worksheet crops")
case(
    "three-overlapping-crops",
    ["c15a", "c15b", "c15c"],
    ["Алгебра, tasks41-44 due18Nov2026", "Exact equations visible in all covering source c15c"],
    ["homework"],
    actions=["solve41-44"],
    audience="group",
    dates=["2026-11-18"],
    numbers=["41", "42", "43", "44", "3", "2", "11", "49", "5", "4", "21", "125"],
    cards=[
        {
            "kind": "homework",
            "subject": "Алгебра",
            "required_terms": ["41", "44"],
            "forbidden_terms": [],
            "deadline": "2026-11-18",
        }
    ],
    display=["c15c"],
)
imgs = []
for n in range(1, 5):
    ident = f"c16{n}"
    save(
        ident,
        page(
            [
                "Алгебра · БИ 1.2",
                f"Страница {n} из 4. К 20.11.2026.",
                f"Решить задачу {50 + n}: x + {n} = {n + 10}.",
                f"Обосновать ответ для задачи {50 + n}.",
            ]
        ),
        "authored distinct workbook page",
    )
    imgs.append(ident)
case(
    "four-distinct-pages",
    imgs,
    ["Four different tasks51,52,53,54, each page has unique equation"],
    ["homework"],
    actions=["solve51-54"],
    audience="group",
    dates=["2026-11-20"],
    numbers=["51", "52", "53", "54", "11", "12", "13", "14"],
    cards=[
        {
            "kind": "homework",
            "subject": "Алгебра",
            "required_terms": ["51", "54"],
            "forbidden_terms": [],
            "deadline": "2026-11-20",
        }
    ],
)

for derived, source_id in [("c03", "c01"), ("c04", "c02"), ("c05", "c02")]:
    item = next(item for item in manifest["images"] if item["id"] == derived)
    source = next(item for item in manifest["images"] if item["id"] == source_id)
    for key in ["author", "license", "source_page", "download_url", "original_sha256"]:
        item[key] = source[key]
    item["derived_from"] = source_id

manifest["gold_provenance"] = (
    "Manually authored expected facts and outcomes, frozen before provider requests; authentic c01/c02 visually inspected. No candidate response is used as gold."
)
manifest["scenarios"] = cases
manifest["snapshot_sha256"] = hashlib.sha256(
    json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
).hexdigest()
(root / "image-complex-manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
)
print(
    json.dumps(
        {
            "scenarios": len(cases),
            "images": len(manifest["images"]),
            "snapshot": manifest["snapshot_sha256"],
        }
    )
)
