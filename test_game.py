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
        self.assertEqual(len(original), 3247)
        self.assertEqual(len(BLOCKS), len(original))
        for record, (kind, content) in zip(BLOCKS, original):
            self.assertEqual(record["kind"], kind)
            if kind == "text":
                self.assertEqual(record["text"], content)
            else:
                image_unit = next(u for u in DATA["units"] if record["id"] in u["blocks"])
                self.assertEqual(image_unit["image"], content)
                self.assertGreater(len(base64.b64decode(content.split(",", 1)[1], validate=True)), 50)

    def test_exactly_once_mapping(self):
        assigned = [i for u in DATA["units"] for i in u["blocks"]]
        self.assertEqual(Counter(assigned), Counter(range(3247)))
        self.assertEqual(len(UNITS), len(DATA["units"]))
        for unit in DATA["units"]:
            if unit["kind"] == "text":
                self.assertEqual(unit["text"], "\n".join(BLOCKS[i]["text"] for i in unit["blocks"]))

    def test_all_knowledge_has_a_question_and_no_exclusions(self):
        active = [u for u in DATA["units"] if u["classification"] == "knowledge"]
        covered = {i for u in active for i in u["blocks"]}
        required = {r["id"] for r in BLOCKS if r["classification"] == "knowledge"}
        self.assertEqual(covered, required)
        self.assertTrue(all(u["question"].strip() for u in active))
        self.assertEqual({u["service"] for u in active}, set(DATA["meta"]["services"]))
        self.assertEqual(len([u for u in active if u["kind"] == "image"]), 50)
        for index in [439, 556, 568, 883, 985, 1184, 1303, 1308, 1754, 1962, 2177, 2787]:
            self.assertNotIn(index, covered)
        for index in [893, 902, 912, 1081, 1085, 1219, 1223, 1781, 2019, 2992, 3004]:
            self.assertIn(index, covered)
        self.assertNotIn(0, covered)
        self.assertNotIn(685, covered)
        self.assertFalse(any(u["kind"] == "text" and re.fullmatch(r"[\s{}\[\],;:()\"'\-]*", u["text"]) for u in active))

    def test_every_objective_answer_has_conceptual_exact_quote(self):
        self.assertEqual(len({q["id"] for q in DATA["questions"]}), len(DATA["questions"]))
        for question in DATA["questions"]:
            self.assertEqual(len(question["options"]), 4)
            self.assertEqual(len(set(question["options"])), 4)
            self.assertEqual(question["options"].count(question["answer"]), 1)
            source = UNITS[question["source"]]
            if question.get("sourceLabel"):
                self.assertEqual(question["service"], "ECR")
                self.assertIn(question["topic"], ["Repository Policy", "Lifecycle policies"])
            else:
                self.assertTrue(any(BLOCKS[i]["classification"] == "knowledge" for i in question.get("quoteBlocks", source["blocks"])))
            if question.get("imageQuote"):
                self.assertEqual(source["kind"], "image")
                self.assertEqual(source["imageNumber"], question["imageNumber"])
                if question.get("imageQuoteType") != "label":
                    self.assertNotIn(question["answer"].casefold(), question["question"].casefold())
            else:
                blocks = question["quoteBlocks"]
                self.assertTrue(blocks)
                self.assertEqual(question["quoteText"], "\n".join(BLOCKS[i]["text"] for i in blocks))
                self.assertTrue(all(BLOCKS[i]["classification"] != "implementation" for i in blocks))
                self.assertTrue(all(BLOCKS[i]["service"] == question["service"] for i in blocks))
        self.assertEqual(DATA["legacy"], [])

    def test_source_identity_and_metadata(self):
        self.assertEqual(hashlib.sha256(RAW.encode("utf-8")).hexdigest(), DATA["meta"]["hash"])
        self.assertEqual(DATA["meta"], AUDIT["meta"])
        self.assertEqual(DATA["meta"]["textBlocks"], 3107)
        self.assertEqual(DATA["meta"]["images"], 140)
        self.assertEqual(len(DATA["meta"]["services"]), 49)
        self.assertEqual(DATA["meta"]["knowledgeUnits"], sum(u["classification"] == "knowledge" for u in DATA["units"]))

    def test_self_contained_and_no_unexpanded_tokens(self):
        self.assertIsNone(GAME.find("script", src=True))
        self.assertIsNone(GAME.find("link", rel="stylesheet"))
        self.assertIsNone(GAME.find("img", src=re.compile(r"^https?://")))
        text = str(GAME)
        self.assertNotIn("__STUDY_DATA__", text)
        self.assertNotIn("__LEGACY_DATA__", text)
        self.assertNotIn("__GAME_RUNTIME__", text)

    def test_authored_coherent_recall_covers_every_knowledge_block(self):
        cards = DATA["cards"] + [u for u in DATA["units"] if u["kind"] == "image" and u["classification"] == "knowledge"]
        self.assertEqual(len(cards), DATA["meta"]["recallCards"])
        self.assertEqual(len({c["id"] for c in cards}), len(cards))
        required = Counter(r["id"] for r in BLOCKS if r["classification"] == "knowledge")
        self.assertEqual(Counter(i for c in cards for i in c["blocks"]), required)
        for card in cards:
            self.assertTrue(card["question"].endswith("?"))
            self.assertNotIn("מה משמעות הפרט המתואר", card["question"])
            self.assertNotIn("אילו תנאים, מגבלות, ברירות מחדל", card["question"])
            self.assertNotIn("מי רשאי לגשת או לפעול", card["question"])
            if card["kind"] == "text":
                self.assertEqual(card["text"], "\n".join(UNITS[i]["text"] for i in card["quoteSources"]))
                self.assertEqual({UNITS[i]["card"] for i in card["quoteSources"]}, {card["id"]})
        self.assertEqual(DATA["meta"]["reviewedTopicGroups"], 493)
        self.assertNotIn("def question_for", (BASE / "build_game.py").read_text(encoding="utf-8"))

    def test_question_quality_and_citation_regressions(self):
        questions = DATA["questions"]
        self.assertEqual(len(questions), 259)
        self.assertEqual({q["service"] for q in questions}, set(DATA["meta"]["services"]))
        self.assertEqual(len({q["question"] for q in questions}), len(questions))
        for question in questions:
            self.assertRegex(question["question"], r"[\u0590-\u05ff]")
            self.assertTrue(question["question"].endswith("?"))
            self.assertTrue(question["text"].strip())
        by_topic = {(q["service"], q["topic"]): q for q in questions}
        cases = [
            (("AWS Organizations", "SCP"), "ה-SCP בלבד לא מספיק"),
            (("AWS IAM Identity Center", "Permission Set"), "role ייעודי"),
            (("VPC", "ENI"), "כרטיס רשת וירטואלי"),
            (("CloudFormation", "Hooks"), "לפני הקמת המשאבים"),
            (("AWS KMS", "HMAC"), "לאימות אמינות ושלמות"),
            (("AWS KMS", "Custom Key Store"), "External Key Store"),
            (("AWS Systems Manager", "OpsItem"), "טיקט בודד נקרא OpsItem"),
            (("AWS Systems Manager", "OpsItem EventBridge"), "ב-EventBridge"),
            (("Amazon Kinesis Data Streams", "Shards"), "1,000 רשומות"),
            (("Other Services by categories", "Connect"), "מערכת טיקטים"),
            (("Other Services by categories", "Service Catalog security"), "תוכנות לא מאושרות"),
        ]
        for key, text in cases:
            self.assertIn(text, by_topic[key]["quoteText"], key)
        for topic in ["Stateful", "Stateless", "מספרי עולה", "Allow וגם Deny", "מחובר לכרטיס הרשת"]:
            self.assertLess(len(by_topic[("VPC", topic)]["quoteBlocks"]), 6)

    def test_simple_ui_defaults_and_runtime_ids(self):
        self.assertEqual(GAME.select_one("#mode option")["value"], "depth")
        self.assertEqual(GAME.select_one("#length option")["value"], "10")
        self.assertEqual(len(GAME.select("nav button:not(.hidden)")), 3)
        runtime = (BASE / "game.js").read_text(encoding="utf-8")
        for identifier in set(re.findall(r'\$\("([^"]+)"\)', runtime)):
            if identifier not in ("tab-", "view-"):
                self.assertIsNotNone(GAME.find(id=identifier), identifier)
        self.assertIn('const STORE = "aws-security-lab-v4-simple"', runtime)
        self.assertIn('localStorage.getItem("aws-security-lab-v3-knowledge")', runtime)


if __name__ == "__main__":
    unittest.main(verbosity=2)
