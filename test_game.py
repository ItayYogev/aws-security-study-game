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
        self.assertEqual(len(original), 3246)
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
        self.assertEqual(Counter(assigned), Counter(range(3246)))
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
        for index in [439, 556, 568, 883, 985, 1184, 1303, 1308, 1754, 1962, 2177, 2786]:
            self.assertNotIn(index, covered)
        for index in [893, 902, 912, 1081, 1085, 1219, 1223, 1781, 2019, 2991, 3003]:
            self.assertIn(index, covered)

    def test_every_objective_answer_has_conceptual_exact_quote(self):
        self.assertEqual(len({q["id"] for q in DATA["questions"]}), len(DATA["questions"]))
        for question in DATA["questions"]:
            self.assertEqual(len(question["options"]), 4)
            self.assertEqual(len(set(question["options"])), 4)
            self.assertEqual(question["options"].count(question["answer"]), 1)
            source = UNITS[question["source"]]
            self.assertEqual(source["classification"], "knowledge")
            if question.get("imageQuote"):
                self.assertEqual(source["kind"], "image")
                self.assertEqual(source["imageNumber"], question["imageNumber"])
                if question.get("imageQuoteType") != "label":
                    clue = question["question"].split("\n", 1)[1]
                    self.assertNotIn(question["answer"].casefold(), clue.casefold())
            else:
                self.assertTrue(source["text"].strip())
                for source_id in question["quoteSources"]:
                    part = UNITS[source_id]
                    self.assertEqual(part["classification"], "knowledge")
                    self.assertEqual(part["text"], "\n".join(BLOCKS[i]["text"] for i in part["blocks"]))
        self.assertEqual(DATA["legacy"], [])

    def test_source_identity_and_metadata(self):
        self.assertEqual(hashlib.sha256(RAW.encode("utf-8")).hexdigest(), DATA["meta"]["hash"])
        self.assertEqual(DATA["meta"], AUDIT["meta"])
        self.assertEqual(DATA["meta"]["textBlocks"], 3106)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
