"""Build the offline game from the reviewed, immutable Google Docs snapshot."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import random
import re

from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
SOURCE_URL = "https://docs.google.com/document/d/1gpBQuOEwNxSLfyhuaXucJzDRDh0ET5We_X9fxCaK1Ak/edit"
REVIEWED_SOURCE_HASH = "a4dbcbdbf5fac2bc96a05c99278c679a23c8014112863ca947868d53ff115942"
CONCEPT_IMAGES = {
    1, 2, 4, 5, 6, 7, 11, 38, 40, 41, 48, 49, 50, 51, 52, 75, 86, 92,
    94, 95, 96, 97, 98, 99, 100, 104, 108, 114, 115, 117, 118, 121,
    122, 123, 124, 125, 126, 127, 128, 130, 131, 132, 133, 134, 135,
    136, 137, 138, 139, 140,
}
# Conceptual statements embedded in console tutorials, retained without testing clicks.
CONCEPT_EXCEPTIONS = {
    891: "AMI", 892: "Root Device", 895: "Quick Start AMIs",
    896: "My AMIs", 897: "Marketplace / Community AMIs", 900: "Instance Types",
    903: "Key Pair", 904: "Key Pair", 905: "Key Pair",
    914: "Security Groups", 923: "Inbound Rules", 924: "Outbound Rules",
    1783: "Metric Filters", 2021: "Custom Data Identifiers",
}
IMPLEMENTATION_RANGES = ((555, 572), (1301, 1314), (1963, 1966), (2791, 2794))
IMPLEMENTATION_BLOCKS = {
    439, 491, 1152, 1245, 1570, 1598, 1756, 1766, 1904, 2068,
    2126, 2166, 2169, 2326, 2761,
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

    if (len(records), image_number, len(services)) != (3252, 140, 49):
        raise ValueError("The source structure changed. Review classifications before rebuilding.")
    # Indexed exceptions belong to this reviewed snapshot, not future downloads.
    if records[2021]["text"].startswith("custom data identifiers:") is False:
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

    sources = json.loads((BASE / "concept-sources.json").read_text(encoding="utf-8"))
    definitions = []
    for line in (BASE / "concept-definitions.txt").read_text(encoding="utf-8").splitlines():
        key, term, answer, category = line.split("|")
        definitions.append({"key": key, "term": term, "answer": answer, "category": category})
    assert len({d["term"] for d in definitions}) == len(definitions), "Duplicate concept"
    terms = {d["term"] for d in definitions}
    conflicts = {term: {term} for term in terms}
    for line in (BASE / "concept-conflicts.txt").read_text(encoding="utf-8").splitlines():
        related = set(line.split("|"))
        assert related <= terms, f"Unknown distractor exclusion: {related - terms}"
        for term in related:
            conflicts[term].update(related)
    reviews = {}
    for key, value in read_catalog("concept-review.txt").items():
        disposition, detail = value.split("|")
        assert disposition in {"alias", "excluded"}
        reviews[key] = {"disposition": disposition, "detail": detail}
    direct = {d["key"] for d in definitions}
    assert not direct.intersection(reviews), "A topic must be directly covered or reviewed, not both"
    assert {str(i) for i in range(493)} == {k for k in direct if k.isdigit()} | set(reviews), "Review every source topic"
    for review in reviews.values():
        if review["disposition"] == "alias":
            assert set(review["detail"].split(",")) <= direct, "Alias must point to covered concepts"
    by_block = {block: unit for unit in units for block in unit["blocks"]}
    images = {u["imageNumber"]: u for u in units if u["kind"] == "image"}
    def source_order(definition):
        citation = sources[definition["key"]]
        blocks = citation["blocks"] if "blocks" in citation else images[citation["imageNumber"]]["blocks"]
        return blocks[0]

    definitions.sort(key=source_order)
    evidence = read_catalog("concept-evidence.txt")
    assert set(evidence) == {k for k in sources if not k.isdigit()}, "Every transcribed chart definition and SRA label must map to a concept"
    assert set(evidence.values()) <= {d["term"] for d in definitions}
    quote_overrides = read_catalog("concept-quotes.txt")
    assert set(quote_overrides) <= {d["term"] for d in definitions}
    questions, cards = [], []
    for definition in definitions:
        key, term, answer, category = (definition[k] for k in ("key", "term", "answer", "category"))
        citation = sources[key]
        peers = [d for d in definitions if d["category"] == category and d["term"] not in conflicts[term]]
        if len(peers) < 3:
            peers += [d for d in definitions if d["category"] != category and d["term"] not in conflicts[term]]
        wrong = random.Random(digest(term)).sample(peers, 3)
        question_text = f"מה עושה {term}?" if term in evidence.values() or term == citation.get("service") else f"מה המשמעות של {term}?"
        question = {
            "id": "concept-" + digest(term + "|" + answer), "topic": term,
            "section": "מה המושג ומה הוא עושה", "kind": "quiz",
            "question": question_text, "answer": answer,
            "options": [answer] + [d["answer"] for d in wrong],
            "distractorTerms": [d["term"] for d in wrong], "category": category,
            "conceptSource": key,
            "text": "הגדרה תמציתית ללמידה. הציטוט המקורי מוצג בנפרד.",
        }
        if "imageNumber" in citation:
            source_unit = images[citation["imageNumber"]]
            question.update(citation, service=source_unit["service"], source=source_unit["id"], quoteBlocks=source_unit["blocks"])
        else:
            blocks = [int(b) for b in quote_overrides[term].split(",")] if term in quote_overrides else citation["blocks"]
            assert set(blocks) <= set(citation["blocks"]), f"Quotation outside reviewed topic: {term}"
            assert blocks and all(records[b]["kind"] == "text" for b in blocks), term
            assert {records[b]["service"] for b in blocks} == {citation["service"]}, term
            source_unit = by_block[blocks[0]]
            question.update(
                service=citation["service"], source=source_unit["id"],
                quoteText="\n".join(records[b]["text"] for b in blocks), quoteBlocks=blocks,
                sourceLabel=key in {"311", "323", "380", "382"} or term == "Break-Glass Account",
            )
        question["evidence"] = [
            dict(sources[k], source=images[sources[k]["imageNumber"]]["id"])
            for k, name in evidence.items() if name == term and k != key
        ]
        for item in question["evidence"]:
            images[item["imageNumber"]].setdefault("concepts", []).append(question["id"])
        if "imageNumber" in citation:
            source_unit.setdefault("concepts", []).append(question["id"])
        questions.append(question)
        card = dict(question, id="recall-" + question["id"], kind="text", text=answer, blocks=question["quoteBlocks"])
        cards.append(card)
        if "blocks" in citation:
            for block in citation["blocks"]:
                by_block[block].setdefault("concepts", []).append(question["id"])
    for key, review in reviews.items():
        if review["disposition"] == "alias":
            concept_ids = [q["id"] for q in questions if q["conceptSource"] in review["detail"].split(",")]
            for block in sources[key]["blocks"]:
                by_block[block].setdefault("concepts", []).extend(concept_ids)
    for unit in units:
        unit["concepts"] = list(dict.fromkeys(unit.get("concepts", [])))
        if unit["concepts"]:
            unit["card"] = unit["concepts"][0]
    active = [u for u in units if u["classification"] == "knowledge"]
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
        "recallCards": len(cards), "reviewedTopicGroups": 493,
        "conceptQuestions": len(questions), "directTopicGroups": len([k for k in direct if k.isdigit()]),
        "aliasedTopicGroups": sum(r["disposition"] == "alias" for r in reviews.values()),
        "excludedTopicGroups": sum(r["disposition"] == "excluded" for r in reviews.values()),
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
        "questions": questions, "topicReviews": reviews, "conceptSources": sources,
        "recallCards": [{k: v for k, v in c.items() if k != "image"} for c in cards],
    }
    (BASE / "coverage-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
