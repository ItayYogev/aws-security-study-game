"""Build the offline game from the reviewed, immutable Google Docs snapshot."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import random
import re

from bs4 import BeautifulSoup, NavigableString

BASE = Path(__file__).resolve().parent
SOURCE_URL = "https://docs.google.com/document/d/1gpBQuOEwNxSLfyhuaXucJzDRDh0ET5We_X9fxCaK1Ak/edit"
REVIEWED_SOURCE_HASH = "2f54cd8d372941d549dd467e3fc2a94353997351933977a6b2967abfc5108928"
CONCEPT_IMAGES = {
    1, 2, 4, 5, 6, 7, 13, 40, 42, 43, 50, 51, 52, 53, 54, 77, 88, 94,
    96, 97, 98, 99, 100, 101, 102, 106, 110, 116, 117, 119, 120, 123,
    124, 125, 126, 127, 128, 129, 130, 132, 133, 134, 135, 136, 137,
    138, 139, 140, 141, 142,
}
IMPLEMENTATION_RANGES = (
    (644, 661), (1454, 1467), (2132, 2135), (3004, 3007),
    (422, 426), (531, 541), (2236, 2238), (3117, 3120),
)
IMPLEMENTATION_BLOCKS = {
    181, 505, 580, 948, 1292, 1388, 1727, 1760, 1920, 1930,
    2068, 2254, 2316, 2356, 2359, 2526, 2974,
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


def source_formatting(tag, styles, css):
    runs = []
    for child in tag.descendants:
        if not isinstance(child, NavigableString):
            continue
        ancestors = []
        parent = child.parent
        while parent is not None:
            ancestors.append(parent)
            if parent is tag:
                break
            parent = parent.parent
        properties = {}
        underline = False
        for ancestor in reversed(ancestors):
            declarations = ";".join(styles.get(c, "") for c in ancestor.get("class", []))
            declarations += ";" + ancestor.get("style", "")
            for declaration in declarations.split(";"):
                if ":" in declaration:
                    key, value = declaration.split(":", 1)
                    key, value = key.strip(), value.strip()
                    if key == "text-decoration" and "underline" in value:
                        underline = True
                    if value not in {"inherit", "unset"}:
                        properties[key] = value
        runs.append({
            "text": str(child),
            "underline": underline or any(a.name == "u" for a in ancestors),
            "bold": properties.get("font-weight") in {"700", "bold"} or any(a.name in {"b", "strong"} for a in ancestors),
            "italic": properties.get("font-style") == "italic" or any(a.name in {"i", "em"} for a in ancestors),
        })
    # Match the exact archive text without disturbing whitespace inside styled spans.
    while runs and not runs[0]["text"].lstrip():
        runs.pop(0)
    while runs and not runs[-1]["text"].rstrip():
        runs.pop()
    if runs:
        runs[0]["text"] = runs[0]["text"].lstrip()
        runs[-1]["text"] = runs[-1]["text"].rstrip()
    result = {"runs": runs}
    if tag.name in {"h1", "h2", "h3"} or "title" in tag.get("class", []):
        result["heading"] = True
    if tag.name == "li":
        listing = tag.parent
        list_class = next((c for c in listing.get("class", []) if c.startswith("lst-kix_")), None)
        if list_class is None:
            raise ValueError("Source list has no reviewed list identity")
        rule = re.search(r"\." + re.escape(list_class) + r">li:before\{([^}]+)\}", css)
        if rule is None:
            raise ValueError(f"Source list has no marker rule: {list_class}")
        counter = re.search(r"counter\([^,]+,([^)]+)\)", rule[1])
        if counter:
            marker = counter[1].strip()
            if marker not in {"decimal", "lower-latin", "upper-latin", "lower-roman"}:
                raise ValueError(f"Unsupported source numbering: {marker}")
        else:
            symbol = re.search(r'content:"\\([0-9a-fA-F]+)', rule[1])
            if symbol is None:
                raise ValueError(f"Unsupported source bullet: {rule[1]}")
            marker = {"25cf": "disc", "25cb": "circle", "25a0": "square"}[symbol[1].lower().lstrip("0")]
        siblings = listing.find_all("li", recursive=False)
        ordinal = int(listing.get("start", 1))
        for sibling in siblings:
            ordinal = int(sibling.get("value", ordinal))
            if sibling is tag:
                break
            ordinal += 1
        result["list"] = {
            "id": list_class, "level": int(list_class.rsplit("-", 1)[1]),
            "ordered": listing.name == "ol", "ordinal": ordinal, "style": marker,
        }
    return result


def build():
    source = BASE / "source-snapshot.html"
    raw = source.read_text(encoding="utf-8")
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != REVIEWED_SOURCE_HASH:
        raise ValueError("Unreviewed source snapshot. Review all changes and classification exceptions before updating the source hash.")
    soup = BeautifulSoup(raw, "html.parser")
    css = soup.style.get_text()
    styles = dict(re.findall(r"\.([a-z]\d+)\{([^}]+)\}", css))
    formatting = {}
    records, services, image_number = [], [], 0
    service, section, topic = "AWS", "General Definitions", "AWS"
    how_to_level = interface_level = None
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
                "interface": interface_level is not None, "howTo": how_to_level is not None,
                "classification": "knowledge" if image_number in CONCEPT_IMAGES and interface_level is None and how_to_level is None else "implementation",
                "reason": "Interface subtree excluded" if interface_level is not None else "How-to section excluded" if how_to_level is not None else "Conceptual drawing or evidence interpretation" if image_number in CONCEPT_IMAGES else "Console screenshot / implementation illustration",
            })
            continue
        if tag.find_parent(["p", "li", "h1", "h2", "h3"]):
            continue
        text = tag.get_text("", strip=False).strip()
        if not text:
            continue
        index = len(records)
        formatting[str(index)] = source_formatting(tag, styles, css)
        assert "".join(run["text"] for run in formatting[str(index)]["runs"]) == text
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
            how_to_level = interface_level = None
            services.append(service)
        elif heading:
            level = int(tag.name[1])
            if re.fullmatch(r"interface\s*:?", text, re.I):
                interface_level = level
            elif interface_level is not None and level <= interface_level:
                interface_level = None
            if "how to" in text.casefold():
                how_to_level = level
            elif how_to_level is not None and level <= how_to_level:
                how_to_level = None
            section = text
            if how_to_level is None and interface_level is None and not STRUCTURAL.match(text):
                topic = text
            elif tag.name in ("h1", "h2") and how_to_level is None:
                topic = service
        elif label and not tag.find_parent("table") and how_to_level is None and interface_level is None and not STRUCTURAL.match(text):
            topic = text
        classification, reason = "knowledge", "Conceptual definition, property, comparison, or example"
        if how_to_level is not None:
            classification, reason = "implementation", "How-to section or endpoint setup sequence"
        if PROCEDURAL.search(text):
            classification, reason = "implementation", "Console/CLI navigation or setup instruction"
        if title or heading or STRUCTURAL.match(text) or label:
            classification, reason = "context", "Heading or mechanism label, attached to its knowledge statements"
        if tag.find_parent("table"):
            topic = "Security Group / NACL"
            classification, reason = "knowledge", "Comparison table cell"
        if index in IMPLEMENTATION_BLOCKS or any(lo <= index <= hi for lo, hi in IMPLEMENTATION_RANGES):
            classification, reason = "implementation", "Reviewed inline setup or console navigation"
        if how_to_level is not None:
            classification, reason = "implementation", "How-to subtree excluded from questions"
        if interface_level is not None:
            classification, reason = "implementation", "Entire Interface subtree excluded from questions"
        records.append({
            "id": index, "kind": "text", "text": text, "service": service,
            "section": section, "topic": topic,
            "interface": interface_level is not None, "howTo": how_to_level is not None,
            "classification": classification, "reason": reason,
            "structural": title or heading or label or bool(STRUCTURAL.match(text)),
        })

    if (len(records), image_number, len(services)) != (3533, 142, 51):
        raise ValueError("The source structure changed. Review classifications before rebuilding.")

    for index in (0, 774):
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
    provenance = json.loads((BASE / "concept-provenance.json").read_text(encoding="utf-8"))
    review_report = json.loads((BASE / "question-review.json").read_text(encoding="utf-8"))
    assert review_report["currentSourceHash"] == REVIEWED_SOURCE_HASH, "Question review belongs to another snapshot"
    assert set(provenance) == {d["term"] for d in definitions}, "Every concept needs explicit source name evidence"
    assert not set(provenance).intersection(review_report["scopeExclusions"]), "Out-of-scope concept reintroduced"
    stable_ids = review_report["stableQuestionIds"]
    assert set(stable_ids) <= set(provenance), "Stable IDs must belong to current concepts"
    assert set(stable_ids.values()) == set(review_report["unchangedQuestionIds"]), "Review every preserved concept ID"
    normalize = lambda text: re.sub(r"[^a-z0-9\u0590-\u05ff]", "", text.casefold())
    for definition in definitions:
        proof = provenance[definition["term"]]
        if "nameBlock" in proof:
            record = records[proof["nameBlock"]]
            assert record["kind"] == "text" and record["text"] == proof["nameQuote"], definition["term"]
            assert normalize(proof["sourceTerm"]) in normalize(record["text"]), definition["term"]
            assert not record["interface"] and not record["howTo"], f"Excluded name evidence: {definition['term']}"
        else:
            citation = sources[definition["key"]]
            assert citation["imageNumber"] == proof["imageNumber"] and citation.get("imageQuoteType") != "label"
            assert normalize(proof["sourceTerm"]) in normalize(citation["imageQuote"]), definition["term"]
    terms = {d["term"] for d in definitions}
    conflicts = {term: {term} for term in terms}
    for line in (BASE / "concept-conflicts.txt").read_text(encoding="utf-8").splitlines():
        related = set(line.split("|"))
        assert related <= terms, f"Unknown distractor exclusion: {related - terms}"
        for term in related:
            conflicts[term].update(related)
    for definition in definitions:
        conflicts[definition["term"]].update(d["term"] for d in definitions if d["key"] == definition["key"])
    reviews = {}
    for key, value in read_catalog("concept-review.txt").items():
        disposition, detail = value.split("|")
        assert disposition in {"alias", "excluded"}
        reviews[key] = {"disposition": disposition, "detail": detail}
    direct = {d["key"] for d in definitions}
    assert not direct.intersection(reviews), "A topic must be directly covered or reviewed, not both"
    topic_keys = {k for k in sources if k.isdigit()}
    assert topic_keys == {k for k in direct if k.isdigit()} | set(reviews), "Review every source topic"
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
    assert {k for k in sources if k.startswith("chart:")} <= set(evidence), "Every transcribed definition must map to a concept"
    assert set(evidence) <= set(sources), "Unknown chart evidence"
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
        source_term = provenance[term]["sourceTerm"]
        service = citation.get("service", "")
        service_name = re.sub(r"^(AWS|Amazon)\s+", "", service, flags=re.I)
        context = "" if service == "Other Services by categories" or normalize(service_name) in normalize(term) or term.startswith(("AWS ", "Amazon ")) else f" ({service})"
        display_term = term + context
        question_text = f"מה עושה המושג הבא: {display_term}?"
        question = {
            "id": stable_ids.get(term, "concept-" + digest(term + "|" + answer)), "topic": term,
            "section": "מה המושג ומה הוא עושה", "kind": "quiz",
            "question": question_text, "answer": answer,
            "options": [answer] + [d["answer"] for d in wrong],
            "distractorTerms": [d["term"] for d in wrong], "category": category,
            "conceptSource": key,
            "sourceTerm": source_term,
            "displayTerm": display_term,
            "nameEvidence": provenance[term],
            "text": "הגדרה תמציתית ללמידה. הציטוט המקורי מוצג בנפרד.",
        }
        if "imageNumber" in citation:
            source_unit = images[citation["imageNumber"]]
            assert not any(records[b]["interface"] or records[b]["howTo"] for b in source_unit["blocks"]), term
            question.update(citation, service=source_unit["service"], source=source_unit["id"], quoteBlocks=source_unit["blocks"])
        else:
            blocks = [int(b) for b in quote_overrides[term].split(",")] if term in quote_overrides else citation["blocks"]
            assert set(blocks) <= set(citation["blocks"]), f"Quotation outside reviewed topic: {term}"
            assert blocks and all(records[b]["kind"] == "text" for b in blocks), term
            assert {records[b]["service"] for b in blocks} == {citation["service"]}, term
            assert not any(records[b]["interface"] or records[b]["howTo"] for b in blocks), f"Excluded quotation: {term}"
            source_unit = by_block[blocks[0]]
            question.update(
                service=citation["service"], source=source_unit["id"],
                quoteText="\n".join(records[b]["text"] for b in blocks), quoteBlocks=blocks,
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
    question_ids = {q["id"] for q in questions}
    question_order = json.loads((BASE / "question-order.json").read_text(encoding="utf-8"))
    assert len(question_order) == len(set(question_order)), "Duplicate permanent question number"
    assert question_ids <= set(question_order), "Append new question IDs to the end of question-order.json; never reorder existing entries"
    number_by_id = {id: index + 1 for index, id in enumerate(question_order)}
    for question, card in zip(questions, cards):
        question["number"] = card["number"] = number_by_id[question["id"]]
    questions.sort(key=lambda q: q["number"])
    cards.sort(key=lambda c: c["number"])
    compatible_revisions = [{
        "sourceHash": review_report["previousSourceHash"],
        "questionIds": review_report["reviewedPreviousIds"],
        "unchangedQuestionIds": review_report["unchangedQuestionIds"],
        "questionIdMap": {entry["previousId"]: stable_ids[entry.get("newTerm", entry["term"])]
                          for entry in review_report["reviewed"] if entry["action"] != "removed"},
    }] + review_report["compatibleRevisions"]
    assert len({r["sourceHash"] for r in compatible_revisions}) == len(compatible_revisions)
    for revision in compatible_revisions:
        assert set(revision["unchangedQuestionIds"]) <= set(revision["questionIds"]) & question_ids
        assert set(revision["questionIdMap"]) <= set(revision["questionIds"])
        assert set(revision["questionIdMap"].values()) <= question_ids
        assert len(set(revision["questionIdMap"].values())) == len(revision["questionIdMap"]), "Do not merge distinct learning records"
    assert all(u["classification"] == "knowledge" for u in active)
    classification_counts = Counter(r["classification"] for r in records)
    meta = {
        "source": SOURCE_URL, "fetched": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat(),
        "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "blocks": len(records),
        "textBlocks": len(records) - image_number, "images": image_number, "services": services,
        "sourceUnits": len(units), "knowledgeUnits": len(active), "depthQuestions": len(questions),
        "recallCards": len(cards), "reviewedTopicGroups": len(topic_keys),
        "quizPolicy": review_report["policy"],
        "plainLanguageReview": review_report["plainLanguageReview"],
        "previousSourceHash": review_report["previousSourceHash"],
        "previousQuestionIds": review_report["reviewedPreviousIds"],
        "unchangedQuestionIds": review_report["unchangedQuestionIds"],
        "compatibleRevisions": compatible_revisions,
        "sourceOnlyReview": {
            "reviewed": review_report["reviewedPreviousQuestions"],
            "removed": len(review_report["removed"]), "corrected": len(review_report["corrected"]),
            "added": len(review_report["added"]),
        },
        "conceptQuestions": len(questions), "directTopicGroups": len([k for k in direct if k.isdigit()]),
        "aliasedTopicGroups": sum(r["disposition"] == "alias" for r in reviews.values()),
        "excludedTopicGroups": sum(r["disposition"] == "excluded" for r in reviews.values()),
        "legacyQuestions": 0, "classifications": dict(classification_counts),
        "conceptImages": len(CONCEPT_IMAGES),
        "numberedSlots": len(question_order),
        "removedQuestionNumbers": [index + 1 for index, id in enumerate(question_order) if id not in question_ids],
    }
    data = {"meta": meta, "sourceFormatting": formatting, "units": units, "cards": [c for c in cards if c["kind"] == "text"], "questions": questions, "legacy": []}
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
    print(json.dumps({k: v for k, v in meta.items() if k not in {
        "previousQuestionIds", "unchangedQuestionIds", "compatibleRevisions",
    }}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
