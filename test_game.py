import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import unittest

from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
RAW = (BASE / "source-snapshot.html").read_text(encoding="utf-8")
SOURCE = BeautifulSoup(RAW, "html.parser")
GAME = BeautifulSoup((BASE / "AWS-Security-Study-Game.html").read_text(encoding="utf-8"), "html.parser")
DATA = json.loads(GAME.find("script", id="study-data").string)
AUDIT = json.loads((BASE / "coverage-audit.json").read_text(encoding="utf-8"))
UNITS = {u["id"]: u for u in DATA["units"]}
BLOCKS = AUDIT["sourceBlocks"]
QUESTIONS = {q["topic"]: q for q in DATA["questions"]}


class GameTests(unittest.TestCase):
    def test_complete_verbatim_archive(self):
        original = []
        for tag in SOURCE.body.find_all(["p", "li", "h1", "h2", "h3", "img"]):
            if tag.name == "img":
                original.append(("image", tag["src"]))
            elif not tag.find_parent(["p", "li", "h1", "h2", "h3"]):
                text = tag.get_text("", strip=False).strip()
                if text:
                    original.append(("text", text))
        self.assertEqual(len(original), 3252)
        self.assertEqual(len(BLOCKS), len(original))
        for record, (kind, content) in zip(BLOCKS, original):
            self.assertEqual(record["kind"], kind)
            if kind == "text":
                self.assertEqual(record["text"], content)
            else:
                unit = next(u for u in DATA["units"] if record["id"] in u["blocks"])
                self.assertEqual(unit["image"], content)
                self.assertGreater(len(base64.b64decode(content.split(",", 1)[1], validate=True)), 50)

    def test_exactly_once_archive_mapping(self):
        assigned = [i for unit in DATA["units"] for i in unit["blocks"]]
        self.assertEqual(Counter(assigned), Counter(range(3252)))
        self.assertEqual(len(UNITS), len(DATA["units"]))
        for unit in DATA["units"]:
            if unit["kind"] == "text":
                self.assertEqual(unit["text"], "\n".join(BLOCKS[i]["text"] for i in unit["blocks"]))
            self.assertTrue(set(unit["concepts"]) <= {q["id"] for q in DATA["questions"]})

    def test_every_reviewed_topic_is_accounted_for(self):
        direct = {q["conceptSource"] for q in DATA["questions"] if q["conceptSource"].isdigit()}
        reviews = AUDIT["topicReviews"]
        self.assertFalse(direct & set(reviews))
        self.assertEqual(direct | set(reviews), {str(i) for i in range(493)})
        for key, review in reviews.items():
            if review["disposition"] == "alias":
                targets = set(review["detail"].split(","))
                self.assertTrue(targets <= direct, key)
                for block in AUDIT["conceptSources"][key]["blocks"]:
                    source = next(u for u in DATA["units"] if block in u["blocks"])
                    self.assertTrue(source["concepts"], key)
            else:
                self.assertEqual(review["disposition"], "excluded")
                self.assertTrue(review["detail"].strip())
        self.assertEqual({q["service"] for q in DATA["questions"]}, set(DATA["meta"]["services"]))

    def test_concept_question_contract(self):
        self.assertEqual(len(QUESTIONS), 636)
        self.assertEqual(len(QUESTIONS), len(DATA["questions"]))
        self.assertEqual(len({q["id"] for q in DATA["questions"]}), len(QUESTIONS))
        conflicts = []
        for line in (BASE / "concept-conflicts.txt").read_text(encoding="utf-8").splitlines():
            conflicts.append(set(line.split("|")))
        for term, question in QUESTIONS.items():
            self.assertIn(question["question"], [f"מה המשמעות של {term}?", f"מה עושה {term}?"])
            self.assertRegex(question["answer"], r"[\u0590-\u05ff]")
            self.assertEqual(len(question["options"]), 4)
            self.assertEqual(len(set(question["options"])), 4)
            self.assertEqual(question["options"].count(question["answer"]), 1)
            self.assertEqual(len(set(question["distractorTerms"])), 3)
            self.assertEqual([QUESTIONS[t]["answer"] for t in question["distractorTerms"]], question["options"][1:])
            for related in conflicts:
                if term in related:
                    self.assertFalse(set(question["distractorTerms"]) & related, term)
            self.assertFalse(re.search(r"\$\d|\d+\s*(?:ימים|שעות|דקות|שניות|בקשות)", question["answer"]), term)
        self.assertEqual(DATA["legacy"], [])

    def test_exact_citations_and_image_transcripts(self):
        for question in DATA["questions"]:
            source = UNITS[question["source"]]
            if question.get("imageQuote"):
                self.assertEqual(source["imageNumber"], question["imageNumber"])
            else:
                blocks = question["quoteBlocks"]
                self.assertTrue(blocks)
                self.assertEqual(question["quoteText"], "\n".join(BLOCKS[b]["text"] for b in blocks))
                self.assertTrue(all(BLOCKS[b]["kind"] == "text" and BLOCKS[b]["service"] == question["service"] for b in blocks))
        evidence = dict(line.split("|") for line in (BASE / "concept-evidence.txt").read_text(encoding="utf-8").splitlines())
        charts = [line.split("|") for line in (BASE / "chart-definitions.txt").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(charts), 67)
        self.assertEqual(len(evidence), 78)
        for number, term, quote in charts:
            question = QUESTIONS[evidence["chart:" + term]]
            citations = [question] + question["evidence"]
            self.assertTrue(any(c.get("imageNumber") == int(number) and c.get("imageQuote") == quote for c in citations), term)
        for key, term in evidence.items():
            if key.startswith("diagram:"):
                self.assertTrue(any(c.get("imageQuote") == key.split(":", 1)[1] and c.get("imageQuoteType") == "label"
                                    for c in [QUESTIONS[term]] + QUESTIONS[term]["evidence"]))

    def test_recall_is_the_same_concept_bank(self):
        self.assertEqual(len(DATA["cards"]), len(QUESTIONS))
        for card, question in zip(DATA["cards"], DATA["questions"]):
            self.assertEqual(card["id"], "recall-" + question["id"])
            self.assertEqual(card["kind"], "text")
            self.assertEqual(card["question"], question["question"])
            self.assertEqual(card["text"], question["answer"])
            self.assertEqual(card["answer"], question["answer"])
            self.assertEqual(card["source"], question["source"])
            self.assertEqual(card["evidence"], question["evidence"])

    def test_current_source_changes_are_visible(self):
        changes = json.loads((BASE / "source-changes.json").read_text(encoding="utf-8"))
        self.assertEqual(changes["currentHash"], DATA["meta"]["hash"])
        self.assertTrue(changes["imagesUnchanged"])
        self.assertEqual(len(changes["changes"]), 5)
        self.assertEqual(changes["textBlocksAfter"] - changes["textBlocksBefore"], 5)
        for term, text in [
            ("Instance Profile", "מתאם בין IAM role ל-instance"),
            ("Instance Profile", "מקבל Temporary Security Credentials מ-STS"),
            ("OpsItem Notifications", "הודעה דרך שירות ההתראות SNS"),
            ("OpsItem Automation Integration", "Automation Runbooks"),
            ("S3 Presigned URL", "פיצ'ר זה עובד על אימות שמבוסס על IAM"),
            ("API Gateway Execution Logs", "Execution Logs - פעולות פנימיות כמו:"),
        ]:
            self.assertIn(text, QUESTIONS[term]["quoteText"], term)
        self.assertNotIn("מרכז בתוכו את הלוגים", QUESTIONS["OpsItem"]["quoteText"])

    def test_source_metadata_and_self_contained_output(self):
        self.assertEqual(hashlib.sha256(RAW.encode()).hexdigest(), DATA["meta"]["hash"])
        self.assertEqual(DATA["meta"], AUDIT["meta"])
        self.assertEqual(DATA["meta"]["textBlocks"], 3112)
        self.assertEqual(DATA["meta"]["images"], 140)
        self.assertEqual(len(DATA["meta"]["services"]), 49)
        self.assertEqual(DATA["meta"]["conceptQuestions"], len(QUESTIONS))
        self.assertEqual(DATA["meta"]["recallCards"], len(QUESTIONS))
        self.assertEqual(DATA["meta"]["directTopicGroups"] + DATA["meta"]["aliasedTopicGroups"] + DATA["meta"]["excludedTopicGroups"], 493)
        self.assertIsNone(GAME.find("script", src=True))
        self.assertIsNone(GAME.find("link", rel="stylesheet"))
        self.assertIsNone(GAME.find("img", src=re.compile(r"^https?://")))
        for token in ["__STUDY_DATA__", "__LEGACY_DATA__", "__GAME_RUNTIME__"]:
            self.assertNotIn(token, str(GAME))

    def test_simple_ui_defaults_and_preserved_old_saves(self):
        self.assertEqual(GAME.select_one("#mode option")["value"], "depth")
        self.assertEqual([o["value"] for o in GAME.select("#mode option")], ["depth", "source"])
        self.assertEqual(GAME.select_one("#length option")["value"], "10")
        self.assertEqual(len(GAME.select("nav button:not(.hidden)")), 3)
        runtime = (BASE / "game.js").read_text(encoding="utf-8")
        for identifier in set(re.findall(r'\$\("([^"]+)"\)', runtime)):
            if identifier not in ("tab-", "view-"):
                self.assertIsNotNone(GAME.find(id=identifier), identifier)
        self.assertIn('const STORE = "aws-security-lab-v5-concepts"', runtime)
        for key in ["aws-security-lab-v4-simple", "aws-security-lab-v3-knowledge"]:
            self.assertIn(f'localStorage.getItem("{key}")', runtime)
            self.assertNotIn(f'localStorage.setItem("{key}"', runtime)
            self.assertNotIn(f'localStorage.removeItem("{key}"', runtime)


if __name__ == "__main__":
    unittest.main(verbosity=2)
