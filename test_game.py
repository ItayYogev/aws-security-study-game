import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
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
    def test_permanent_number_ledger_is_append_only(self):
        ledger = json.loads((BASE / "question-order.json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256("\n".join(ledger[:467]).encode()).hexdigest(),
                         "e541c0911a0805cd11ab91ef8e1869f4dc62b1fe78848d7bebd2ef44f199830d")
        self.assertEqual(len(ledger), len(set(ledger)))
        for question in DATA["questions"]:
            self.assertEqual(ledger[question["number"] - 1], question["id"])
        self.assertEqual([q["number"] for q in DATA["questions"]], sorted(q["number"] for q in DATA["questions"]))
        self.assertEqual(DATA["meta"]["numberedSlots"], len(ledger))
        self.assertEqual(DATA["meta"]["removedQuestionNumbers"],
                         [i + 1 for i, id in enumerate(ledger) if id not in {q["id"] for q in DATA["questions"]}])

    def test_complete_verbatim_archive(self):
        original = []
        for tag in SOURCE.body.find_all(["p", "li", "h1", "h2", "h3", "img"]):
            if tag.name == "img":
                original.append(("image", tag["src"]))
            elif not tag.find_parent(["p", "li", "h1", "h2", "h3"]):
                text = tag.get_text("", strip=False).strip()
                if text:
                    original.append(("text", text))
        self.assertEqual(len(original), 3422)
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
        self.assertEqual(Counter(assigned), Counter(range(3422)))
        self.assertEqual(len(UNITS), len(DATA["units"]))
        for unit in DATA["units"]:
            if unit["kind"] == "text":
                self.assertEqual(unit["text"], "\n".join(BLOCKS[i]["text"] for i in unit["blocks"]))
            self.assertTrue(set(unit["concepts"]) <= {q["id"] for q in DATA["questions"]})

    def test_all_source_formatting_is_preserved(self):
        formats = DATA["sourceFormatting"]
        tags = [tag for tag in SOURCE.body.find_all(["p", "li", "h1", "h2", "h3", "img"])
                if tag.name == "img" or (not tag.find_parent(["p", "li", "h1", "h2", "h3"])
                                         and tag.get_text("", strip=False).strip())]
        self.assertEqual(len(formats), 3280)
        underline_count = 0
        numbered_starts = set()
        levels = set()
        for index, tag in enumerate(tags):
            if tag.name == "img":
                self.assertNotIn(str(index), formats)
                continue
            format = formats[str(index)]
            self.assertEqual("".join(run["text"] for run in format["runs"]), BLOCKS[index]["text"])
            underline_count += sum(run["underline"] for run in format["runs"])
            self.assertEqual("list" in format, tag.name == "li")
            if tag.name == "li":
                listing = tag.parent
                metadata = format["list"]
                siblings = listing.find_all("li", recursive=False)
                position = next(i for i, sibling in enumerate(siblings) if sibling is tag)
                self.assertEqual(metadata["ordinal"], int(listing.get("start", 1)) + position)
                self.assertEqual(metadata["ordered"], listing.name == "ol")
                levels.add(metadata["level"])
                if listing.name == "ol":
                    numbered_starts.add(metadata["ordinal"])
                else:
                    self.assertIn(metadata["style"], {"disc", "circle", "square"})
        self.assertEqual(sum("list" in f for f in formats.values()),
                         sum(bool(li.get_text("", strip=False).strip()) for li in SOURCE.find_all("li")))
        self.assertEqual(levels, {0, 1, 2})
        self.assertGreater(max(numbered_starts), 1)
        self.assertGreater(underline_count, 100)
        styles = dict(re.findall(r"\.([a-z]\d+)\{([^}]+)\}", SOURCE.style.get_text()))
        for index, tag in enumerate(tags):
            if tag.name != "img":
                for span in tag.select("span"):
                    if any("text-decoration:underline" in styles.get(c, "") for c in span.get("class", [])):
                        text = span.get_text().strip()
                        if text:
                            underlined = "".join(run["text"] for run in formats[str(index)]["runs"] if run["underline"])
                            self.assertIn(text, underlined)

    def test_every_reviewed_topic_is_accounted_for(self):
        direct = {q["conceptSource"] for q in DATA["questions"] if q["conceptSource"].isdigit()}
        reviews = AUDIT["topicReviews"]
        self.assertFalse(direct & set(reviews))
        self.assertEqual(direct | set(reviews), {k for k in AUDIT["conceptSources"] if k.isdigit()})
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
        self.assertEqual(len(QUESTIONS), 467)
        self.assertEqual(len(QUESTIONS), len(DATA["questions"]))
        self.assertEqual(len({q["id"] for q in DATA["questions"]}), len(QUESTIONS))
        conflicts = []
        for line in (BASE / "concept-conflicts.txt").read_text(encoding="utf-8").splitlines():
            conflicts.append(set(line.split("|")))
        for term, question in QUESTIONS.items():
            source_term = question["sourceTerm"]
            self.assertEqual(question["question"], f"מה עושה המושג הבא: {question['displayTerm']}?")
            self.assertIn(term, question["displayTerm"])
            self.assertRegex(question["answer"], r"[\u0590-\u05ff]")
            self.assertEqual(len(question["options"]), 4)
            self.assertEqual(len(set(question["options"])), 4)
            self.assertEqual(question["options"].count(question["answer"]), 1)
            self.assertEqual(len(set(question["distractorTerms"])), 3)
            self.assertEqual([QUESTIONS[t]["answer"] for t in question["distractorTerms"]], question["options"][1:])
            for related in conflicts:
                if term in related:
                    self.assertFalse(set(question["distractorTerms"]) & related, term)
            for distractor in question["distractorTerms"]:
                self.assertNotEqual(QUESTIONS[distractor]["conceptSource"], question["conceptSource"])
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
        self.assertEqual(len(evidence), 70)
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

    def test_source_only_names_and_complete_review(self):
        normalize = lambda text: re.sub(r"[^a-z0-9\u0590-\u05ff]", "", text.casefold())
        provenance = json.loads((BASE / "concept-provenance.json").read_text(encoding="utf-8"))
        report = json.loads((BASE / "question-review.json").read_text(encoding="utf-8"))
        self.assertEqual(set(provenance), set(QUESTIONS))
        for term, question in QUESTIONS.items():
            proof = provenance[term]
            self.assertEqual(question["nameEvidence"], proof)
            self.assertEqual(question["sourceTerm"], proof["sourceTerm"])
            self.assertFalse(question.get("sourceLabel"))
            self.assertNotEqual(question.get("imageQuoteType"), "label")
            if "nameBlock" in proof:
                self.assertEqual(proof["nameQuote"], BLOCKS[proof["nameBlock"]]["text"])
                self.assertIn(normalize(proof["sourceTerm"]), normalize(proof["nameQuote"]))
            else:
                self.assertIn(normalize(proof["sourceTerm"]), normalize(question["imageQuote"]))
        self.assertEqual(len(report["reviewed"]), 460)
        self.assertEqual(len({r["previousId"] for r in report["reviewed"]}), 460)
        self.assertEqual(len(report["removed"]), 1)
        self.assertEqual(len(report["corrected"]), 5)
        self.assertEqual(len(report["added"]), 8)
        self.assertEqual(len(report["unchangedQuestionIds"]), 459)
        previous = {r["term"]: r for r in report["reviewed"]}
        retained = {entry.get("newTerm", term) for term, entry in previous.items() if entry["action"] != "removed"}
        self.assertEqual(retained | set(report["added"]), set(QUESTIONS))
        for term, old in previous.items():
            if old["action"] != "removed":
                target = old.get("newTerm", term)
                self.assertEqual(QUESTIONS[target]["id"], old["previousId"])
        for entry in report["removed"]:
            self.assertNotIn(entry["term"], QUESTIONS)
            self.assertTrue(entry["reason"])
        for entry in report["corrected"]:
            self.assertEqual(QUESTIONS[entry["newTerm"]]["answer"], entry["afterAnswer"])
        self.assertEqual(set(DATA["meta"]["unchangedQuestionIds"]), set(report["unchangedQuestionIds"]))
        self.assertEqual(set(DATA["meta"]["previousQuestionIds"]), set(report["reviewedPreviousIds"]))
        self.assertEqual(len(QUESTIONS), 460 - len(report["removed"]) + len(report["added"]))
        self.assertTrue(any(h["reviewedPreviousQuestions"] == 636 for h in report["history"]))
        self.assertTrue(any(h["reviewedPreviousQuestions"] == 611 for h in report["history"]))
        self.assertEqual(report["currentSourceHash"], DATA["meta"]["hash"])

    def test_plain_language_answers_and_added_mechanisms(self):
        review = DATA["meta"]["plainLanguageReview"]
        self.assertEqual(review["reviewedPreviousAnswers"], 460)
        self.assertEqual(review["rewrittenAnswers"], 5)
        self.assertEqual(review["newAnswers"], 8)
        answers = {q["answer"] for q in QUESTIONS.values()}
        for question in QUESTIONS.values():
            self.assertLessEqual(len(question["answer"].split()), review["maximumWords"], question["topic"])
            self.assertLessEqual(len(question["answer"]), review["maximumCharacters"], question["topic"])
            self.assertFalse(re.search(r"\b(?:תזמור|אחזור|יישויות|ישות|תצורה)\b", question["answer"]), question["topic"])
            self.assertTrue(set(question["options"]) <= answers)
        for term in ["Cross-Region Backup Copying", "AWS Backup Vault", "Standard Vault",
                     "Logically Air-Gapped Vault", "AWS Backup Vault Lock", "Volume Gateway",
                     "File Gateway", "AWS Elastic Disaster Recovery"]:
            self.assertIn(term, QUESTIONS)
        for term in ["AWS Marketplace", "User Access Portal", "IAM Access Analyzer Custom Policy Checks",
                     "IAM Access Analyzer Policy Generation", "Cross-account access", "WAF Rule Group",
                     "WAF Rule", "חוקים מחנות AWS Marketplace", "GuardDuty Custom Entity Lists"]:
            self.assertIn(term, QUESTIONS)
        self.assertNotIn("Internal Access Analysis", QUESTIONS)
        self.assertNotIn("GuardDuty Entity List", QUESTIONS)
        self.assertEqual(QUESTIONS["AWS Backup"]["service"], "AWS Backup")
        self.assertEqual(QUESTIONS["AWS Storage Gateway"]["service"], "Storage Gateway")
        self.assertIn("לקבוצה", QUESTIONS["Inline Policy"]["answer"])

    def test_aws_only_scope_and_entire_interface_subtrees(self):
        interface_level = how_to_level = None
        excluded = set()
        index = 0
        for tag in SOURCE.body.find_all(["p", "li", "h1", "h2", "h3", "img"]):
            if tag.name != "img" and tag.find_parent(["p", "li", "h1", "h2", "h3"]):
                continue
            text = tag.get_text("", strip=False).strip() if tag.name != "img" else ""
            if tag.name != "img" and not text:
                continue
            if "title" in tag.get("class", []):
                interface_level = how_to_level = None
            if tag.name in ("h1", "h2", "h3"):
                level = int(tag.name[1])
                if re.fullmatch(r"interface\s*:?", text, re.I):
                    interface_level = level
                elif interface_level is not None and level <= interface_level:
                    interface_level = None
                if "how to" in text.casefold():
                    how_to_level = level
                elif how_to_level is not None and level <= how_to_level:
                    how_to_level = None
            if interface_level is not None or how_to_level is not None:
                excluded.add(index)
                self.assertEqual(BLOCKS[index]["classification"], "implementation")
            self.assertEqual(BLOCKS[index]["interface"], interface_level is not None)
            self.assertEqual(BLOCKS[index]["howTo"], how_to_level is not None)
            index += 1
        self.assertEqual(sum(b["interface"] for b in BLOCKS), 302)
        for question in DATA["questions"]:
            evidence = set(question["quoteBlocks"])
            if "nameBlock" in question["nameEvidence"]:
                evidence.add(question["nameEvidence"]["nameBlock"])
            for citation in question["evidence"]:
                evidence.update(UNITS[citation["source"]]["blocks"])
            self.assertFalse(evidence & excluded, question["topic"])
        report = json.loads((BASE / "question-review.json").read_text(encoding="utf-8"))
        self.assertFalse(set(QUESTIONS) & set(report["scopeExclusions"]))
        for term in ["Server", "CapEx", "Latency", "CIDR", "JWT", "MQTT",
                     "Kubernetes Deployment Spec", "Billing Alarm", "CloudWatch Dashboard",
                     "Stealth Finding", "Root Device", "Macie Finding", "Compliant", "non_compliant"]:
            self.assertNotIn(term, QUESTIONS)
        for term in ["AWS KMS", "Amazon EC2", "IAM Role", "Security Group",
                     "AWS STS", "Interface Endpoint", "CloudWatch Metric Filter",
                     "Inbound Rules", "CloudWatchAgentServerPolicy"]:
            self.assertIn(term, QUESTIONS)
        self.assertTrue(all(DATA["meta"]["quizPolicy"].values()))

    def test_progress_revision_compatibility(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const data = JSON.parse(fs.readFileSync("AWS-Security-Study-Game.html","utf8")
  .match(/<script id="study-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const runtime = fs.readFileSync("game.js","utf8");
const functions = runtime.slice(runtime.indexOf("function validProgress("), runtime.indexOf("function load("));
const read = new Function("DATA","playableIds", 'const UNKNOWN_ANSWER="__unknown__";\n' + functions + "\nreturn readSave;")
  (data, new Set([...data.cards,...data.questions].map(q=>q.id)));
const unchanged = data.meta.unchangedQuestionIds[0];
const removed = data.meta.previousQuestionIds.find(id=>!data.meta.unchangedQuestionIds.includes(id));
const value = {attempts:2,successes:1,streak:1,last:2,starred:true,updated:"2026-10-20"};
const round = ids=>({ids,index:0,score:0,missed:[],answered:false,revealed:false,choice:"",note:""});
const saved = {version:5,sourceHash:data.meta.previousSourceHash,
  progress:{[unchanged]:value,["recall-"+unchanged]:value,[removed]:value},session:round([unchanged,removed])};
const original = JSON.stringify(saved);
const migrated = read(saved);
assert.deepEqual(Object.keys(migrated.progress),[unchanged,"recall-"+unchanged]);
assert.deepEqual(migrated.archivedProgress,{[removed]:value});
assert.deepEqual(migrated.completedQuestionIds,[unchanged]);
assert.deepEqual(read({...saved,completedQuestionIds:[]}).completedQuestionIds,[]);
assert.deepEqual(migrated.session,round([unchanged]));
assert.equal(migrated.migrated,true);
assert.ok(migrated.notice);
assert.equal(JSON.stringify(saved),original);
assert.deepEqual(read({...saved,session:round([unchanged])}).session,round([unchanged]));
assert.equal(read({...saved,session:round([removed])}).session,null);
const answered = {...round([unchanged,removed]),answered:true,revealed:true,score:1,choice:"old answer"};
const resumed = read({...saved,session:answered}).session;
assert.equal(resumed.answered,true);
assert.equal(resumed.score,1);
assert.equal(resumed.choice,data.questions.find(q=>q.id===unchanged).answer);
const skipDeleted = {...round([removed,unchanged]),answered:true,revealed:true,score:1};
assert.deepEqual(read({...saved,session:skipDeleted}).session,round([unchanged]));
assert.throws(()=>read({...saved,progress:{invalid:value}}));
assert.throws(()=>read({...saved,sourceHash:"unknown"}));
assert.throws(()=>read({...saved,version:4}));
const current = {version:5,sourceHash:data.meta.hash,progress:{[unchanged]:value},session:null};
assert.equal(read(current).migrated,false);
assert.deepEqual(read(current).progress,current.progress);
assert.deepEqual(read(current).completedQuestionIds,[unchanged]);
assert.deepEqual(read({...current,completedQuestionIds:[]}).completedQuestionIds,[]);
assert.throws(()=>read({...current,completedQuestionIds:[removed]}));
assert.throws(()=>read({...current,completedQuestionIds:[unchanged,unchanged]}));
assert.throws(()=>read({...current,progress:{[removed]:value}}));
assert.deepEqual(read({...current,archivedProgress:{[removed]:value}}).archivedProgress,{[removed]:value});
assert.throws(()=>read({...current,archivedProgress:{[removed]:{attempts:-1}}}));
for (const revision of data.meta.compatibleRevisions) {
  const stable = Object.keys(revision.questionIdMap)[0];
  const retired = revision.questionIds.find(id=>!revision.questionIdMap[id]);
  const changed = Object.keys(revision.questionIdMap).find(id=>revision.questionIdMap[id]!==id);
  assert.ok(stable);
  const oldIds = [stable,changed,retired].filter((id,index,ids)=>id && ids.indexOf(id)===index);
  const prior = {...saved,sourceHash:revision.sourceHash,
    progress:Object.fromEntries(oldIds.flatMap(id=>[[id,value],["recall-"+id,value]])),
    session:round(oldIds)};
  const result = read(prior);
  for (const id of oldIds) {
    const mapped = revision.questionIdMap[id];
    assert.deepEqual(mapped?result.progress[mapped]:result.archivedProgress[id],value);
    assert.deepEqual(mapped?result.progress["recall-"+mapped]:result.archivedProgress["recall-"+id],value);
  }
  assert.deepEqual(result.session.ids,oldIds.map(id=>revision.questionIdMap[id]).filter(Boolean));
  assert.equal(result.migrated,true);
  assert.deepEqual(read({...prior,session:round(["recall-"+stable])}).session.ids,["recall-"+revision.questionIdMap[stable]]);
  assert.throws(()=>read({...prior,progress:{foreign:value}}));
  const full = {...prior,progress:Object.fromEntries(revision.questionIds.map(id=>[id,value])),
    session:round(revision.questionIds)};
  const all = read(full);
  assert.equal(Object.keys(all.progress).length + Object.keys(all.archivedProgress).length,revision.questionIds.length);
  assert.equal(all.session.ids.length,Object.keys(revision.questionIdMap).length);
  assert.deepEqual(new Set(all.completedQuestionIds),new Set(Object.values(revision.questionIdMap)));
}
"""
        result = subprocess.run(["node", "-e", script], cwd=BASE, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_migration_backs_up_original_and_never_overwrites_on_storage_failure(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const data = JSON.parse(fs.readFileSync("AWS-Security-Study-Game.html","utf8")
  .match(/<script id="study-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const runtime = fs.readFileSync("game.js","utf8");
const source = runtime.slice(runtime.indexOf("function validProgress("),runtime.indexOf("function getProgress("));
const revision = data.meta.compatibleRevisions[0];
const id = Object.keys(revision.questionIdMap)[0];
const value = {attempts:1,successes:1,streak:1,last:2,starred:false,updated:"2026-10-23"};
const original = JSON.stringify({version:5,sourceHash:revision.sourceHash,progress:{[id]:value},session:null});
const key = "aws-security-lab-v5-concepts";
const backupKey = key + "-backup-" + revision.sourceHash;
const run = new Function("DATA","localStorage",`
const STORE="${key}",UNKNOWN_ANSWER="__unknown__";
const playableIds=new Set([...DATA.questions,...DATA.cards].map(q=>q.id));
let progress={},archivedProgress={},completedQuestionIds=new Set(),storedSession=null,storageFailed=false,message="";
const $=()=>({textContent:"",classList:{remove(){}}});
function reportStorage(text){storageFailed=true;message=text;}
${source}
load();
return {progress,archivedProgress,storedSession,storageFailed,message};
`);
const normal = new Map([[key,original]]);
const store = map => ({getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v)});
assert.equal(run(data,store(normal)).storageFailed,false);
assert.equal(normal.get(backupKey),original);
assert.equal(JSON.parse(normal.get(key)).sourceHash,data.meta.hash);
assert.deepEqual(JSON.parse(normal.get(key)).progress,{[revision.questionIdMap[id]]:value});
run(data,store(normal));
assert.equal(normal.get(backupKey),original);
const failed = new Map([[key,original]]);
const result = run(data,{getItem:k=>failed.get(k)??null,setItem:()=>{throw new Error("storage full");}});
assert.equal(result.storageFailed,true);
assert.ok(result.message.includes("storage full"));
assert.equal(failed.get(key),original);
assert.equal(failed.has(backupKey),false);
const existing = new Map([[key,original],[backupKey,"original backup"]]);
run(data,store(existing));
assert.equal(existing.get(backupKey),"original backup");
"""
        result = subprocess.run(["node", "-e", script], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_current_source_changes_are_visible(self):
        changes = json.loads((BASE / "source-changes.json").read_text(encoding="utf-8"))
        self.assertEqual(changes["currentHash"], DATA["meta"]["hash"])
        self.assertFalse(changes["imagesUnchanged"])
        self.assertTrue(changes["previousImagesRetained"])
        self.assertEqual(changes["addedImages"], [8, 10])
        self.assertEqual(len(changes["changes"]), 19)
        self.assertEqual(changes["textBlocksAfter"] - changes["textBlocksBefore"], 78)
        self.assertEqual(Counter(c["operation"] for c in changes["changes"]), {"replace": 13, "insert": 4, "delete": 2})
        for term, text in [
            ("Instance Profile", "מתאם בין IAM role ל-instance"),
            ("Instance Profile", "מקבל Temporary Security Credentials מ-STS"),
            ("S3 Presigned URL", "מבלי לדרוש מהאדם להזדהות עם משתמש AWS"),
            ("API Gateway Execution Logs", "Execution Logs - פעולות פנימיות כמו:"),
            ("Resource Tags", "Key-Value Pairs"),
            ("ABAC", "Attribute-Based Access Control"),
            ("Source/Destination Check", "המנגנון הזה מובנה בתוך ה-ENI"),
            ("Federated user", "משתמש שמגיע ממערכת חיצונית"),
            ("Default NACL", "אי אפשר להשאיר סאבנט"),
            ("NAT Gateway", "רכיב שמאפשר למשאבים"),
            ("Public NAT Gateway", "ה-Public NAT Gateway מוקם ב-Public Subnet"),
            ("Private NAT Gateway", "Non-routable subnet"),
            ("AWS CloudTrail Insights", "אפשר לשלב אותו עם EventBridge + SNS"),
            ("CloudWatch Logs Insights", "אך ורק על דאטה"),
            ("CloudWatch Metric Filter", "מילים או דפוסים"),
            ("Custom Insights", "לפי חוקים שהמשתמש"),
            ("Managed Insights", "ש-AWS החליטו"),
            ("Multi-Region KMS Key", "MRK"),
            ("DB Instance", "השרת שמריץ"),
            ("RDS Multi-AZ", "העותק הראשי"),
            ("RDS Multi-AZ", "כל עותק מוקם ב-subnet אחר"),
            ("IoT Thing Name", "שם המכשיר."),
            ("AWS IoT Policy", "ל-Thing Name שלו."),
            ("Amazon DocumentDB", "שירות לניהול"),
            ("ECS Agent", "סוכן שיושב בתוך ה-EC2"),
            ("Agent Communication Service (ACS)", "מנגנון שיושב בצד של AWS"),
            ("Amazon ElastiCache", "בזיכרון RAM"),
            ("VPC Endpoint", "בלי לעבור באינטרנט הציבורי"),
            ("GuardDutyExcluded", "לא מונע מ-GuardDuty לזהות אירועים אחרים"),
            ("CloudWatchAgentServerPolicy", "CloudWatchAgentServerPolicy"),
            ("ECS Service", "ECS Service"),
            ("ECS Task Definition", "Task Definition"),
            ("AWS Backup", "תוכנית גיבוי"),
            ("AWS Storage Gateway", "תיקיית רשת מקומית"),
            ("AWS Backup Vault", "מיכל האחסון הלוגי"),
            ("Standard Vault", "בתוך חשבון"),
            ("Logically Air-Gapped Vault", "נפרד ומבודד"),
            ("AWS Backup Vault Lock", "בלתי ניתנים לשינוי או מחיקה"),
            ("Volume Gateway", "כונן קשיח"),
            ("File Gateway", "תיקיית רשת"),
            ("AWS Elastic Disaster Recovery", "AWS DRS"),
            ("AWS Marketplace", "חנות דיגיטלית"),
            ("User Access Portal", "פורטל התחברות מרכזי"),
            ("IAM Access Analyzer Custom Policy Checks", "בודק הרשאות"),
            ("IAM Access Analyzer Policy Generation", "בהתבסס על מה שהוא עשה בעבר"),
            ("Cross-account access", "בחשבון AWS אחר"),
            ("WAF Rule Group", "קבוצה שמכילה"),
            ("חוקים מחנות AWS Marketplace", "חברות אבטחה חיצוניות"),
        ]:
            self.assertIn(text, QUESTIONS[term]["quoteText"], term)
        self.assertNotIn("מרכז בתוכו את הלוגים", QUESTIONS["OpsItem"]["quoteText"])
        self.assertNotIn("מאומת", QUESTIONS["AWS IoT Policy"]["quoteText"])
        self.assertNotIn("כפתור סימון", QUESTIONS["RDS Multi-AZ"]["quoteText"])
        self.assertNotIn("מתרגם כתובות חד-כיווני", QUESTIONS["NAT Gateway"]["quoteText"])
        self.assertNotIn("Insights (CloudTrail)", QUESTIONS)
        self.assertIn("התראה", QUESTIONS["AWS CloudTrail Insights"]["answer"])
        self.assertIn("רשימה של התוכנות", QUESTIONS["Systems Manager Inventory"]["answer"])
        self.assertNotIn("זהות או לתעודה", QUESTIONS["AWS IoT Policy"]["answer"])
        self.assertIn("בצד AWS", QUESTIONS["Agent Communication Service (ACS)"]["answer"])
        self.assertNotIn("ECS Agent ACS", QUESTIONS)
        self.assertNotIn("רכיב בסוכן", QUESTIONS["Agent Communication Service (ACS)"]["answer"])
        self.assertEqual(QUESTIONS["Inbound Rules"]["service"], "VPC")
        self.assertEqual(QUESTIONS["CloudWatch Metric Filter"]["nameEvidence"]["nameBlock"], 1825)

    def test_source_metadata_and_self_contained_output(self):
        self.assertEqual(hashlib.sha256(RAW.encode()).hexdigest(), DATA["meta"]["hash"])
        self.assertEqual(DATA["meta"], AUDIT["meta"])
        self.assertEqual(DATA["meta"]["textBlocks"], 3280)
        self.assertEqual(DATA["meta"]["images"], 142)
        self.assertEqual(len(DATA["meta"]["services"]), 51)
        self.assertEqual(DATA["meta"]["conceptQuestions"], len(QUESTIONS))
        self.assertEqual(DATA["meta"]["recallCards"], len(QUESTIONS))
        self.assertEqual(DATA["meta"]["directTopicGroups"] + DATA["meta"]["aliasedTopicGroups"] + DATA["meta"]["excludedTopicGroups"], 521)
        self.assertIsNone(GAME.find("script", src=True))
        self.assertIsNone(GAME.find("link", rel="stylesheet"))
        self.assertIsNone(GAME.find("img", src=re.compile(r"^https?://")))
        for token in ["__STUDY_DATA__", "__LEGACY_DATA__", "__GAME_RUNTIME__"]:
            self.assertNotIn(token, str(GAME))

    def test_simple_ui_defaults_and_preserved_old_saves(self):
        self.assertEqual(GAME.select_one("#mode option")["value"], "depth")
        self.assertEqual([o["value"] for o in GAME.select("#mode option")], ["depth", "source"])
        self.assertEqual(GAME.select_one("#length option")["value"], "all")
        self.assertEqual(GAME.select_one("#length option[selected]")["value"], "all")
        self.assertEqual(len(GAME.select("nav button:not(.hidden)")), 3)
        self.assertEqual(GAME.select_one("#unknown-answer").get_text(), "לא יודע את התשובה")
        self.assertEqual(GAME.select_one("#unknown-answer")["type"], "button")
        self.assertNotIn(GAME.select_one("#unknown-answer"), GAME.select("#options button"))
        runtime = (BASE / "game.js").read_text(encoding="utf-8")
        for identifier in set(re.findall(r'\$\("([^"]+)"\)', runtime)):
            if identifier not in ("tab-", "view-"):
                self.assertIsNotNone(GAME.find(id=identifier), identifier)
        self.assertIn('const STORE = "aws-security-lab-v5-concepts"', runtime)
        for key in ["aws-security-lab-v4-simple", "aws-security-lab-v3-knowledge"]:
            self.assertIn(f'localStorage.getItem("{key}")', runtime)
            self.assertNotIn(f'localStorage.setItem("{key}"', runtime)
            self.assertNotIn(f'localStorage.removeItem("{key}"', runtime)

    def test_unknown_answer_is_recorded_for_review_once(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const runtime = fs.readFileSync("game.js", "utf8");
const source = runtime.slice(runtime.indexOf("function answerQuiz("), runtime.indexOf("function next("));
const session = {answered:false};
const grades = [];
let shown = 0;
const unknown = JSON.parse(runtime.match(/const UNKNOWN_ANSWER = ("[^"]+");/)[1]);
const answerQuiz = new Function("session", "current", "record", "showAnswer", "UNKNOWN_ANSWER",
  source + "\nreturn answerQuiz;")(session, () => ({answer:"correct"}),
  grade => {grades.push(grade); session.answered = true;}, () => {shown++;}, unknown);
answerQuiz(unknown);
assert.equal(session.choice, unknown);
assert.equal(session.revealed, true);
assert.deepEqual(grades, [0]);
assert.equal(shown, 1);
answerQuiz("correct");
assert.deepEqual(grades, [0]);
assert.equal(session.choice, unknown);
"""
        result = subprocess.run(["node", "-e", script], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_each_answer_is_saved_automatically_with_archive_and_resume(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const runtime = fs.readFileSync("game.js","utf8");
const data = JSON.parse(fs.readFileSync("AWS-Security-Study-Game.html","utf8")
  .match(/<script id="study-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const source = runtime.slice(runtime.indexOf("function save()"),runtime.indexOf("function updateStats()"));
const recordSource = runtime.slice(runtime.indexOf("function record("),runtime.indexOf("function answerQuiz("));
const readSource = runtime.slice(runtime.indexOf("function validProgress("),runtime.indexOf("function load("));
const storage = new Map();
const localStorage = {setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)??null};
const $ = () => ({classList:{add(){},remove(){}},focus(){}});
const question = data.questions[0];
const archive = {"retired-question":{attempts:1,successes:1,streak:1,last:2,starred:false,updated:"2026-10-20"}};
const run = new Function("DATA","localStorage","$","current","archive",`
const STORE="aws-security-lab-v5-concepts";
const UNKNOWN_ANSWER="__unknown__";
const playableIds=new Set([...DATA.questions,...DATA.cards].map(q=>q.id));
let progress={},archivedProgress=archive,completedQuestionIds=new Set(),storedSession=null,storageFailed=false;
const session={ids:[current().id],index:0,score:0,missed:[],answered:false,revealed:true,choice:current().answer,note:""};
function updateStats(){}
function reportStorage(message){throw new Error(message);}
${source}
${recordSource}
${readSource}
record(2);
const raw=localStorage.getItem(STORE);
const restored=readSave(JSON.parse(raw));
record(2);
return {raw,restored,afterDuplicate:localStorage.getItem(STORE)};
`);
const result = run(data,localStorage,$,()=>question,archive);
const saved = JSON.parse(result.raw);
assert.equal(saved.progress[question.id].successes,1);
assert.equal(saved.progress[question.id].attempts,1);
assert.equal(saved.progress[question.id].last,2);
assert.deepEqual(saved.completedQuestionIds,[]);
assert.equal(saved.session.answered,true);
assert.equal(saved.session.score,1);
assert.deepEqual(saved.archivedProgress,archive);
assert.deepEqual(result.restored.progress,saved.progress);
assert.deepEqual(result.restored.session,saved.session);
assert.deepEqual(result.restored.archivedProgress,archive);
assert.equal(result.afterDuplicate,result.raw);
"""
        result = subprocess.run(["node", "-e", script], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixed_numbers_and_jump_ignore_device_progress(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const runtime = fs.readFileSync("game.js","utf8");
const source = runtime.slice(runtime.indexOf("function updateStats("),runtime.indexOf("function recallDeck("));
const elements=new Map();
const $=id=>{
  if(!elements.has(id)) elements.set(id,{value:"",textContent:"",hidden:false,
    classList:{toggle(name,value){elements.get(id).hidden=value;}}});
  return elements.get(id);
};
const DATA={questions:[{id:"a",number:1},{id:"b",number:3}],legacy:[],meta:{recallCards:2,images:0,numberedSlots:3}};
const result=new Function("DATA","$",
`let progress={a:{successes:3,starred:true}},archivedProgress={old:{successes:1}};
let completedQuestionIds=new Set(["a"]),session={},storedSession={};
let started;
function getProgress(){return {attempts:0};}
function recallDeck(){return [];}
function fillServices(){}
function modeDeck(){return DATA.questions;}
function begin(ids){started=ids;}
${source}
updateStats();
const first=$("bank-progress").textContent;
$("jump-number").value="2";
jumpToQuestion();
const skipped={ids:started,message:$("jump-status").textContent};
$("jump-number").value="0";
started=null;
jumpToQuestion();
const invalid={ids:started,message:$("jump-status").textContent};
return {first,skipped,invalid,progress};
`)(DATA,$);
assert.ok(result.first.includes("2")&&result.first.includes("3"));
assert.deepEqual(result.skipped.ids,["b"]);
assert.ok(result.skipped.message.includes("2")&&result.skipped.message.includes("3"));
assert.equal(result.invalid.ids,null);
assert.ok(result.invalid.message);
assert.equal(result.progress.a.successes,3);
"""
        result = subprocess.run(["node", "-e", script], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_round_contains_every_available_question(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const html = fs.readFileSync("AWS-Security-Study-Game.html", "utf8");
const data = JSON.parse(html.match(/<script id="study-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const runtime = fs.readFileSync("game.js", "utf8");
const source = runtime.slice(runtime.indexOf("function begin("), runtime.indexOf("function current("));
const defaults = Object.fromEntries(["length"].map(id => {
  const select = html.match(new RegExp('<select id="' + id + '">([\\s\\S]*?)</select>'))[1];
  const options = [...select.matchAll(/<option value="([^"]+)"([^>]*)>/g)];
  return [id, (options.find(option => /\bselected\b/.test(option[2])) || options[0])[1]];
}));
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {value:defaults[id], classList:{add(){}, remove(){}}});
  return elements.get(id);
};
let saved = null;
const run = new Function("$", "filterDeck", "shuffle", "openTab", "getProgress", `
let session;
function saveSession() {}
function render() {}
${source}
begin();
return session;
`);
assert.equal(defaults.length, "all");
const start = deck => run($, () => deck, deck => [...deck].reverse(), () => {}, () => ({}));
saved = start(data.questions);
assert.equal(saved.ids.length, 467);
assert.deepEqual(new Set(saved.ids), new Set(data.questions.map(question => question.id)));
assert.deepEqual(saved.ids, data.questions.map(question => question.id));
const filtered = data.questions.filter(question => question.service === "AWS Backup");
assert.equal(start(filtered).ids.length, filtered.length);
$("length").value = "10";
assert.equal(start(data.questions).ids.length, 10);
assert.deepEqual(start(data.questions).ids, data.questions.map(question => question.id).slice(0,10));
"""
        result = subprocess.run(["node", "-e", script], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_round_uses_permanent_order(self):
        self.assertIsNone(GAME.select_one("#order"))
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const runtime = fs.readFileSync("game.js", "utf8");
const shuffleSource = runtime.match(/function shuffle\([\s\S]*?\r?\n}/)[0];
const randomValues = [0, 0.99];
const shuffle = new Function("Math", shuffleSource + "\nreturn shuffle;")({
  floor:Math.floor, random:() => randomValues[currentRun]
});
const source = runtime.slice(runtime.indexOf("function begin("), runtime.indexOf("function current("));
const deck = Array.from({length:20}, (_,i) => ({id:String(i),number:i+1}));
const byId = new Map(deck.map(q => [q.id,q]));
const playableIds = new Set(byId.keys());
let currentRun = 0;
const $ = id => ({value:id==="length"?"10":undefined,classList:{add(){},remove(){}}});
const run = new Function("$", "filterDeck", "shuffle", "openTab", "byId", "playableIds", "ids", `
let session;
function saveSession() {}
function render() {}
${source}
begin(ids);
return session;
`);
const start = ids => run($, () => deck, shuffle, () => {}, byId, playableIds, ids);
const first = start();
currentRun = 1;
const second = start();
assert.deepEqual(first.ids, second.ids);
assert.deepEqual(first.ids,deck.slice(0,10).map(q=>q.id));
assert.equal(new Set(first.ids).size, 10);
const explicit = ["15","3","12","8"];
currentRun = 0;
const retry = start(explicit);
assert.deepEqual(new Set(retry.ids), new Set(explicit));
assert.notDeepEqual(retry.ids, explicit);
assert.deepEqual(retry.ids,["3","8","12","15"]);
assert.deepEqual(explicit, ["15","3","12","8"]);
const original = deck.map(q=>q.id);
assert.deepEqual(original, Array.from({length:20}, (_,i)=>String(i)));
"""
        result = subprocess.run(["node", "-e", script], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_vault_lock_note_keeps_the_exact_quote_separate(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const html = fs.readFileSync("AWS-Security-Study-Game.html", "utf8");
const data = JSON.parse(html.match(/<script id="study-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const runtime = fs.readFileSync("game.js", "utf8");
const source = runtime.slice(runtime.indexOf("function corrections("), runtime.indexOf("function renderNotes("));
const corrections = new Function(source + "\nreturn corrections;")();
const question = data.questions.find(question => question.topic === "AWS Backup Vault Lock");
const unit = data.units.find(unit => unit.id === question.source);
const notes = corrections({...unit, text:question.quoteText});
assert.equal(notes.length, 1);
assert.ok(notes[0].includes("Governance"));
assert.ok(notes[0].includes("Compliance"));
assert.ok(question.quoteText.includes("כולל מנהל המערכת"));
assert.ok(!question.answer.includes("אף אחד"));
"""
        result = subprocess.run(["node", "-e", script], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_quotes_use_fixed_rtl_not_first_character_direction(self):
        style = GAME.find("style").get_text()
        declarations = {}
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style):
            if selector.strip() == ".source-text":
                declarations.update(item.strip().split(":", 1) for item in body.split(";") if ":" in item)
        self.assertEqual(declarations["direction"], "rtl")
        self.assertEqual(declarations["text-align"], "right")
        self.assertEqual(declarations["unicode-bidi"], "isolate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
