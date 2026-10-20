"""Build the offline game from the reviewed, immutable Google Docs snapshot."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import re

from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
SOURCE_URL = "https://docs.google.com/document/d/1gpBQuOEwNxSLfyhuaXucJzDRDh0ET5We_X9fxCaK1Ak/edit"
REVIEWED_SOURCE_HASH = "6b04f308595fc8f4e6eb9ce7cfa8deeb0944cf5e4f77f89b12b9190b23199674"
CONCEPT_IMAGES = {
    1, 2, 4, 5, 6, 7, 11, 38, 40, 41, 48, 49, 50, 51, 52, 75, 86, 92,
    94, 95, 96, 97, 98, 99, 100, 104, 108, 114, 115, 117, 118, 121,
    122, 123, 124, 125, 126, 127, 128, 130, 131, 132, 133, 134, 135,
    136, 137, 138, 139, 140,
}
# Conceptual statements embedded in console tutorials, retained without testing clicks.
CONCEPT_EXCEPTIONS = {
    889: "AMI", 890: "Root Device", 893: "Quick Start AMIs",
    894: "My AMIs", 895: "Marketplace / Community AMIs", 898: "Instance Types",
    901: "Key Pair", 902: "Key Pair", 903: "Key Pair",
    912: "Security Groups", 921: "Inbound Rules", 922: "Outbound Rules",
    1781: "Metric Filters", 2019: "Custom Data Identifiers",
}
IMPLEMENTATION_RANGES = ((555, 572), (1299, 1312), (1961, 1964), (2786, 2789))
IMPLEMENTATION_BLOCKS = {
    439, 491, 1150, 1243, 1568, 1596, 1754, 1764, 1902, 2066,
    2124, 2164, 2167, 2324, 2756,
}
PROCEDURAL = re.compile(
    r"^(?:\d+[.)]\s*)?(?:נכנס|ניכנס|לחיצה|נלחץ|לוחצים|לחצו|יש ללחוץ|"
    r"נבחר|בוחרים|יש לבחור|בחירה של|סימון של|נסמן|מסמנים|נלחץ|נגדיר|"
    r"נלחץ|יש להיכנס|להיכנס לקטגוריה|לאחר מכן נלחץ|מעבר לעמוד|כניסה לקטגוריה|"
    r"לסמן תיבת סימון|מקימים|יוצרים רכיב|מוסיפים להגדרות|שלב \d)"
    r"|(?:כדי לעשות את זה|כדי לעשות זאת|כדי למצוא את ה-ARN|תהליך היישום|"
    r"יש ללחוץ|יש להיכנס|נלחץ על|לאחר מכן נחזור|לוחצים על)",
    re.I,
)
STRUCTURAL = re.compile(
    r"^(?:הערות חשובות|הערה חשובה|יכולות|דוגמאות|דוגמה|יתרונות|חסרונות|"
    r"הגדרות|הסבר|איך זה עובד|איך הוא עובד|יש שני סוגים|ממה .* מורכב|"
    r"מה הבעיה|מה זה|מתי להשתמש|General Definitions|Definitions|"
    r"Common actions|Interface|Capabilities|Pricing model|Services|"
    r"מאפיין|שכבת ההגנה|ניהול מצב|סדר פעולת החוקים|הגדרת מקור/יעד|כמויות ושיוך)"
    r"\s*[:?!]?$", re.I,
)


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def read_catalog(filename):
    rows = {}
    for line in (BASE / filename).read_text(encoding="utf-8").splitlines():
        if line.strip():
            key, value = line.split("|", 1)
            if key in rows:
                raise ValueError(f"Duplicate catalog entry in {filename}: {key}")
            rows[key] = value
    return rows


def build():
    source = BASE / "source-snapshot.html"
    raw = source.read_text(encoding="utf-8")
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != REVIEWED_SOURCE_HASH:
        raise ValueError("Unreviewed source snapshot. Review all changes and classification exceptions before updating the source hash.")
    soup = BeautifulSoup(raw, "html.parser")
    styles = dict(re.findall(r"\.([a-z]\d+)\{([^}]+)\}", soup.style.get_text()))
    records, services, image_number = [], [], 0
    service, section, topic = "AWS", "General Definitions", "AWS"
    how_to_level = None
    for tag in soup.body.find_all(["p", "li", "h1", "h2", "h3", "img"]):
        if tag.name == "img":
            image_number += 1
            image = tag.get("src", "")
            if not re.match(r"^data:image/(png|jpeg|jpg|gif|webp);base64,", image):
                raise ValueError(f"Image {image_number} is not embedded")
            base64.b64decode(image.split(",", 1)[1], validate=True)
            records.append({
                "id": len(records), "kind": "image", "text": "", "service": service,
                "section": section, "topic": topic, "image": image, "imageNumber": image_number,
                "classification": "knowledge" if image_number in CONCEPT_IMAGES else "implementation",
                "reason": "Conceptual drawing or evidence interpretation" if image_number in CONCEPT_IMAGES else "Console screenshot / implementation illustration",
            })
            continue
        if tag.find_parent(["p", "li", "h1", "h2", "h3"]):
            continue
        text = tag.get_text("", strip=False).strip()
        if not text:
            continue
        index = len(records)
        title = "title" in tag.get("class", [])
        heading = tag.name in ("h1", "h2", "h3")
        bold_length = sum(
            len(span.get_text()) for span in tag.select("span")
            if any("font-weight:700" in styles.get(c, "") for c in span.get("class", []))
        )
        label = not title and not heading and len(text) < 200 and bold_length >= len(text) * .7
        if title:
            service = topic = text
            section = ""
            how_to_level = None
            services.append(service)
        elif heading:
            level = int(tag.name[1])
            if "how to" in text.casefold():
                how_to_level = level
            elif how_to_level is not None and level <= how_to_level:
                how_to_level = None
            section = text
            if how_to_level is None and not STRUCTURAL.match(text) and text.casefold() != "interface":
                topic = text
            elif tag.name in ("h1", "h2") and how_to_level is None:
                topic = service
        elif label and not tag.find_parent("table") and how_to_level is None and not STRUCTURAL.match(text):
            topic = text
        classification, reason = "knowledge", "Conceptual definition, property, comparison, or example"
        if how_to_level is not None:
            classification, reason = "implementation", "How-to section or endpoint setup sequence"
        if PROCEDURAL.search(text):
            classification, reason = "implementation", "Console/CLI navigation or setup instruction"
        if title or heading or STRUCTURAL.match(text) or label:
            classification, reason = "context", "Heading or mechanism label, attached to its knowledge statements"
        if index in CONCEPT_EXCEPTIONS:
            classification, reason = "knowledge", "Conceptual statement retained from a how-to section"
        if tag.find_parent("table"):
            topic = "Security Group / NACL"
            classification, reason = "knowledge", "Comparison table cell"
        if index in IMPLEMENTATION_BLOCKS or any(lo <= index <= hi for lo, hi in IMPLEMENTATION_RANGES):
            classification, reason = "implementation", "Reviewed inline setup or console navigation"
        records.append({
            "id": index, "kind": "text", "text": text, "service": service,
            "section": section, "topic": CONCEPT_EXCEPTIONS.get(index, topic),
            "classification": classification, "reason": reason,
            "structural": title or heading or label or bool(STRUCTURAL.match(text)),
        })

    if (len(records), image_number, len(services)) != (3247, 140, 49):
        raise ValueError("The source structure changed. Review classifications before rebuilding.")
    # Indexed exceptions belong to this reviewed snapshot, not future downloads.
    if records[2019]["text"].startswith("custom data identifiers:") is False:
        raise ValueError("Reviewed exception indices no longer match the snapshot")

    for index in (0, 685):
        records[index].update(classification="context", reason="Document navigation link, not study content")
    position = 0
    while position < len(records):
        first = records[position]
        if first["kind"] != "text" or first["text"].strip() not in ("{", "["):
            position += 1
            continue
        balance, end = 0, position
        while end < len(records) and records[end]["service"] == first["service"]:
            text = records[end]["text"]
            balance += text.count("{") + text.count("[") - text.count("}") - text.count("]")
            if balance == 0:
                break
            end += 1
        if end >= len(records) or balance != 0:
            raise ValueError(f"Unbalanced source code example at block {position + 1}")
        for record in records[position:end + 1]:
            record["codeGroup"] = position
        position = end + 1

    units, pending = [], []

    def flush():
        if not pending:
            return
        first = pending[0]
        text = "\n".join(r["text"] for r in pending if r["text"])
        identity = "|".join([first["service"], first["topic"], text, str(first["id"])])
        unit = {
            "id": "src-" + digest(identity), "service": first["service"],
            "section": first["section"], "topic": first["topic"], "kind": first["kind"],
            "text": text, "blocks": [r["id"] for r in pending],
            "classification": first["classification"], "reason": first["reason"],
        }
        if first["kind"] == "image":
            unit.update(image=first["image"], imageNumber=first["imageNumber"])
            nearby = [
                r for r in records[max(0, first["id"] - 3): first["id"] + 4]
                if r["kind"] == "text" and r["service"] == first["service"]
                and r["classification"] == "knowledge"
            ]
            unit["text"] = "\n".join(r["text"] for r in nearby)
            unit["quoteBlocks"] = [r["id"] for r in nearby]
        units.append(unit)
        pending.clear()

    for record in records:
        same = pending and all(record[k] == pending[0][k] for k in ("service", "topic", "classification"))
        same_code = pending and "codeGroup" in record and record["codeGroup"] == pending[0].get("codeGroup")
        table = record["topic"] == "Security Group / NACL"
        if pending and not same_code and (not same or record["kind"] == "image" or pending[0]["kind"] == "image"
                        or (not table and (sum(len(r["text"]) for r in pending) > 300 or len(pending) >= 3))):
            flush()
        pending.append(record)
    flush()

    prompts = read_catalog("recall-questions.txt")
    image_prompts = read_catalog("image-questions.txt")
    groups = []
    for unit in units:
        if unit["classification"] != "knowledge" or unit["kind"] != "text":
            continue
        key = tuple(unit[k] for k in ("service", "section", "topic"))
        if not groups or groups[-1][0] != key:
            groups.append((key, []))
        groups[-1][1].append(unit)
    assert set(prompts) == {str(i) for i in range(len(groups))}, "Review each topic group after changing the source"
    cards = []
    for index, (key, parts) in enumerate(groups):
        question = prompts[str(index)]
        if question == "-":
            for part in parts:
                part["classification"] = "context"
                part["reason"] = "Reviewed navigation or section introduction; no independent conceptual content"
                for block in part["blocks"]:
                    records[block].update(classification="context", reason=part["reason"])
            continue
        text = "\n".join(part["text"] for part in parts)
        card = {
            "id": "recall-" + digest("|".join(key) + "|" + text),
            "service": key[0], "section": key[1], "topic": key[2], "kind": "text",
            "classification": "knowledge", "question": question, "text": text,
            "blocks": [block for part in parts for block in part["blocks"]],
            "source": parts[0]["id"], "quoteSources": [part["id"] for part in parts],
        }
        cards.append(card)
        for part in parts:
            part.update(question=question, card=card["id"])
    for unit in units:
        if unit["classification"] == "knowledge" and unit["kind"] == "image":
            unit["question"] = image_prompts[str(unit["imageNumber"])]
            unit["card"] = unit["id"]
            cards.append(unit)
    assert {int(n) for n in image_prompts} == CONCEPT_IMAGES
    active = [u for u in units if u["classification"] == "knowledge"]
    citations = {}
    for line in (BASE / "question-sources.txt").read_text(encoding="utf-8").splitlines():
        svc, anchor, block_list = line.split("|")
        key = (svc, anchor)
        if key in citations:
            raise ValueError(f"Duplicate question citation: {key}")
        citations[key] = [int(block) for block in block_list.split(",")]
    questions = []
    used_citations = set()
    for line in (BASE / "depth-questions.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fields = line.split("|")
        if len(fields) != 8:
            raise ValueError("Invalid authored question: " + line)
        svc, anchor, question, answer, *tail = fields
        wrong = tail[:3]
        explain = tail[3]
        key = (svc, anchor)
        blocks = citations[key]
        used_citations.add(key)
        quote_records = [records[index] for index in blocks]
        assert all(r["kind"] == "text" and r["service"] == svc and r["classification"] != "implementation" for r in quote_records), key
        label_only = (svc, anchor) in {("ECR", "Repository Policy"), ("ECR", "Lifecycle policies")}
        assert label_only or any(r["classification"] == "knowledge" for r in quote_records), key
        matched = next(u for u in units if blocks[0] in u["blocks"])
        questions.append({
            "id": "deep-" + digest(question), "service": svc, "section": "שאלות הבנה",
            "topic": anchor, "kind": "quiz", "question": question, "answer": answer,
            "options": [answer] + wrong, "text": explain, "source": matched["id"],
            "quoteText": "\n".join(r["text"] for r in quote_records), "quoteBlocks": blocks,
            "sourceLabel": label_only,
        })
    assert used_citations == set(citations), "Unused question citations must be reviewed"

    chart_rows = []
    for line in (BASE / "chart-definitions.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            number, term, quote = line.split("|")
            chart_rows.append((int(number), term, quote))
    chart_questions = read_catalog("chart-questions.txt")
    assert set(chart_questions) == {term for _, term, _ in chart_rows}
    for number, term, quote in chart_rows:
        source_unit = next(u for u in units if u.get("imageNumber") == number)
        question, *alternatives = chart_questions[term].split("|")
        assert len(alternatives) == 3
        questions.append({
            "id": "chart-" + digest(term), "service": source_unit["service"],
            "section": "הגדרות שירותים מתוך התרשימים", "topic": term, "kind": "quiz",
            "question": question,
            "answer": term, "options": [term] + alternatives,
            "text": f"השירות המתאים הוא {term}. הציטוט המקורי באנגלית מופיע להלן; ניסוח השאלה בעברית הוא עיבוד של ההגדרה.",
            "source": source_unit["id"], "imageQuote": quote, "imageNumber": number,
        })
    for line in (BASE / "diagram-questions.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        number, label, question, answer, wrong1, wrong2, wrong3, explain = line.split("|")
        source_unit = next(u for u in units if u.get("imageNumber") == int(number))
        questions.append({
            "id": "diagram-" + digest(question), "service": source_unit["service"],
            "section": "שירותים בארכיטקטורת האבטחה", "topic": label, "kind": "quiz",
            "question": question, "answer": answer, "options": [answer, wrong1, wrong2, wrong3],
            "text": explain, "source": source_unit["id"], "imageQuote": label,
            "imageQuoteType": "label", "imageNumber": int(number),
        })
    assigned = [i for u in units for i in u["blocks"]]
    assert Counter(assigned) == Counter(range(len(records))), "Every source block must be archived exactly once"
    assert all(len(set(q["options"])) == 4 for q in questions), "Four distinct answers required"
    assert len({q["id"] for q in questions}) == len(questions), "Question IDs must be unique"
    assert all(u["classification"] == "knowledge" for u in active)
    classification_counts = Counter(r["classification"] for r in records)
    meta = {
        "source": SOURCE_URL, "fetched": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat(),
        "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "blocks": len(records),
        "textBlocks": len(records) - image_number, "images": image_number, "services": services,
        "sourceUnits": len(units), "knowledgeUnits": len(active), "depthQuestions": len(questions),
        "recallCards": len(cards), "reviewedTopicGroups": len(groups),
        "legacyQuestions": 0, "classifications": dict(classification_counts),
        "conceptImages": len(CONCEPT_IMAGES),
    }
    data = {"meta": meta, "units": units, "cards": [c for c in cards if c["kind"] == "text"], "questions": questions, "legacy": []}
    template = (BASE / "game-template.html").read_text(encoding="utf-8")
    output = template.replace("__STUDY_DATA__", json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))
    output = output.replace("__LEGACY_DATA__", "[]")
    output = output.replace("__GAME_RUNTIME__", (BASE / "game.js").read_text(encoding="utf-8"))
    assert "__STUDY_DATA__" not in output
    (BASE / "AWS-Security-Study-Game.html").write_text(output, encoding="utf-8")
    audit = {
        "meta": meta, "allBlocksMappedExactlyOnce": True, "allImagesIncluded": True,
        "sourceBlocks": [{k: v for k, v in r.items() if k != "image"} for r in records],
        "sourceUnits": [{k: v for k, v in u.items() if k != "image"} for u in units],
        "questions": questions,
        "recallCards": [{k: v for k, v in c.items() if k != "image"} for c in cards],
    }
    (BASE / "coverage-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
