"use strict";
const DATA = JSON.parse(document.getElementById("study-data").textContent);
const $ = id => document.getElementById(id);
const ALL = [...DATA.units, ...DATA.cards, ...DATA.questions, ...DATA.legacy];
const byId = new Map(ALL.map(unit => [unit.id, unit]));
const playableIds = new Set([...DATA.cards, ...DATA.questions].map(unit => unit.id));
const STORE = "aws-security-lab-v5-concepts";
const UNKNOWN_ANSWER = "__unknown__";
const modes = {
  source: "אותם מושגים בשליפה עצמית: הסבר מהו המושג ומה תפקידו, ואז השווה להגדרה ולציטוט.",
  depth: "מה עושה המושג של AWS? ארבע תשובות לבחירה ואפשרות ״לא יודע את התשובה״. בלי מושגי יסוד כלליים, תוכן Interface או הוראות יישום."
};
let progress = {};
let archivedProgress = {};
let completedQuestionIds = new Set();
let session = null;
let storedSession = null;
let libraryPage = 0;
let quizOptions = [];
let storageFailed = false;
function node(tag, text, cls) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (cls) result.className = cls;
  return result;
}
function sourceText(text, blocks, tag = "blockquote") {
  const container = node(tag, undefined, "source-text");
  const formats = (blocks || []).map(id => {
    const format = DATA.sourceFormatting[id];
    if (!format) throw new Error(`Missing source formatting for block ${id}`);
    return format;
  });
  if (!formats.length) {
    container.textContent = text;
    return container;
  }
  const exactText = formats.map(format => format.runs.map(run => run.text).join("")).join("\n");
  if (exactText !== text) throw new Error("Source formatting does not match the quoted text");
  container.classList.add("structured-source");
  let previousList = null;
  formats.forEach((format, index) => {
    let block;
    if (format.list) {
      const list = format.list;
      if (!previousList || previousList.id !== list.id || previousList.ordinal + 1 !== list.ordinal) {
        if (index) container.append(document.createTextNode("\n"));
        const element = node(list.ordered ? "ol" : "ul", undefined, "source-list");
        element.style.listStyleType = list.style;
        element.style.marginInlineStart = `${list.level * 14}px`;
        element.dataset.level = String(list.level);
        if (list.ordered) element.start = list.ordinal;
        container.append(element);
        previousList = {id: list.id, element, ordinal: list.ordinal};
      } else previousList.element.append(document.createTextNode("\n"));
      block = node("li", undefined, "source-block");
      previousList.element.append(block);
      previousList.ordinal = list.ordinal;
    } else {
      if (index) container.append(document.createTextNode("\n"));
      previousList = null;
      block = node("div", undefined, "source-block");
      container.append(block);
    }
    if (format.heading) block.classList.add("source-heading");
    format.runs.forEach(run => {
      const span = node("span", run.text);
      if (run.underline) span.classList.add("source-underline");
      if (run.bold) span.classList.add("source-bold");
      if (run.italic) span.classList.add("source-italic");
      block.append(span);
    });
  });
  return container;
}
function shuffle(items) {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}
function canonicalTerm(term) {
  const groups = [
    ["AWS KMS", "AWS Key Management Service (KMS)"],
    ["Amazon Data Firehose", "Amazon Kinesis Data Firehose"],
    ["Amazon EMR", "Amazon Elastic MapReduce (EMR)"],
    ["AWS Certificate Manager (ACM)", "AWS Certificate Manager"],
    ["AWS Managed Microsoft AD", "AWS Directory Service for Microsoft AD"],
    ["Amazon Cloud Directory", "AWS Cloud Directory"],
    ["AWS STS AssumeRole", "AWS Security Token Service (STS)"]
  ];
  const group = groups.find(items => items.includes(term));
  return group ? group[0] : term;
}
function reportStorage(message) {
  storageFailed = true;
  $("storage-error").textContent = message;
  $("storage-error").classList.remove("hidden");
}
function validProgress(value, requireKnownIds = true) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return Object.entries(value).every(([id, p]) => (!requireKnownIds || playableIds.has(id)) && p && typeof p === "object" &&
    Number.isInteger(p.attempts) && p.attempts >= 0 &&
    Number.isInteger(p.successes) && p.successes >= 0 && p.successes <= p.attempts &&
    Number.isInteger(p.streak) && p.streak >= 0 && p.streak <= p.successes &&
    [0, 1, 2].includes(p.last) && typeof p.starred === "boolean" && typeof p.updated === "string");
}
function validSession(value, ids = playableIds) {
  return value && Array.isArray(value.ids) && value.ids.length > 0 &&
    value.ids.every(id => ids.has(id)) && new Set(value.ids).size === value.ids.length &&
    Number.isInteger(value.index) && value.index >= 0 && value.index < value.ids.length &&
    Number.isInteger(value.score) && value.score >= 0 && value.score <= value.index + 1 &&
    Array.isArray(value.missed) && value.missed.every(id => value.ids.includes(id)) &&
    typeof value.answered === "boolean" && typeof value.revealed === "boolean" &&
    typeof value.choice === "string" && typeof value.note === "string";
}
function readSave(saved) {
  if (!saved || saved.version !== 5) throw new Error("מבנה נתוני השמירה אינו מתאים לגרסה זו.");
  const archive = saved.archivedProgress === undefined ? {} : saved.archivedProgress;
  if (!validProgress(archive, false)) throw new Error("ארכיון ההתקדמות אינו תקין.");
  const completed = saved.completedQuestionIds === undefined ?
    [...new Set(Object.entries(saved.progress || {}).filter(([, p]) => p.successes > 0)
      .map(([id]) => id.startsWith("recall-") ? id.slice(7) : id))] : saved.completedQuestionIds;
  if (!Array.isArray(completed) || completed.some(id => typeof id !== "string") ||
      new Set(completed).size !== completed.length) throw new Error("נתוני השלמת השאלות אינם תקינים.");
  if (saved.sourceHash === DATA.meta.hash) {
    if (!validProgress(saved.progress) || (saved.session !== null && !validSession(saved.session))) {
      throw new Error("נתוני ההתקדמות או הסבב השמור אינם תקינים.");
    }
    if (completed.some(id => !playableIds.has(id) || id.startsWith("recall-"))) throw new Error("מזהי השלמת השאלות אינם תקינים.");
    return {progress:saved.progress, archivedProgress:archive, completedQuestionIds:completed, session:saved.session, notice:"", migrated:false};
  }
  const revision = DATA.meta.compatibleRevisions.find(item => item.sourceHash === saved.sourceHash);
  if (!revision) throw new Error("השמירה אינה תואמת לגרסת הסיכום.");
  const previousIds = new Set(revision.questionIds.flatMap(id => [id, "recall-" + id]));
  if (completed.some(id => !revision.questionIds.includes(id))) throw new Error("מזהי השלמת השאלות הקודמות אינם תקינים.");
  if (!validProgress(saved.progress, false) || Object.keys(saved.progress).some(id => !previousIds.has(id)) ||
      (saved.session !== null && !validSession(saved.session, previousIds))) {
    throw new Error("נתוני הגרסה הקודמת אינם תקינים.");
  }
  const mapId = id => {
    const recall = id.startsWith("recall-");
    const previousId = recall ? id.slice(7) : id;
    const target = revision.questionIdMap[previousId];
    return target ? (recall ? "recall-" : "") + target : null;
  };
  const kept = {};
  const archived = {...archive};
  for (const [id, value] of Object.entries(saved.progress)) {
    const target = mapId(id);
    if (target) kept[target] = value;
    else archived[id] = value;
  }
  let resumed = null;
  if (saved.session) {
    const previous = saved.session;
    const mapped = previous.ids.map(mapId);
    const nextIndex = mapped.findIndex((id, index) => index >= previous.index && id !== null);
    if (nextIndex !== -1) {
      const ids = mapped.filter(id => id !== null);
      const sameQuestion = nextIndex === previous.index;
      const completed = previous.ids.slice(0, previous.index + Number(previous.answered));
      const completedKept = completed.filter(id => mapId(id));
      const answered = sameQuestion && previous.answered;
      const choice = answered && previous.choice !== UNKNOWN_ANSWER &&
        !previous.missed.includes(previous.ids[previous.index]) ?
        (DATA.questions.find(q => q.id === mapped[nextIndex])?.answer || previous.choice) : previous.choice;
      resumed = {...previous, ids, index:mapped.slice(0, nextIndex).filter(Boolean).length,
        score:completedKept.filter(id => !previous.missed.includes(id)).length,
        missed:previous.missed.map(mapId).filter(Boolean),
        answered, revealed:sameQuestion && previous.revealed, choice:sameQuestion ? choice : "",
        note:sameQuestion ? previous.note : ""};
      if (!validSession(resumed)) throw new Error("לא ניתן להתאים את נקודת העצירה למאגר המעודכן.");
    }
  }
  const notice = `מאגר השאלות עודכן. ההתקדמות נשמרה ב-${Object.keys(kept).length} פריטי תרגול, גם בשאלות שנוסחו מחדש. ${Object.keys(archived).length} רשומות של שאלות שהוסרו נשמרות בארכיון ואינן נספרות במאגר הפעיל. ${resumed ? "נקודת העצירה נשמרה; שאלות חדשות נוספו לסוף המאגר." : "אפשר להתחיל סבב חדש בלי לאפס את ההתקדמות."}`;
  return {progress:kept, archivedProgress:archived,
    completedQuestionIds:completed.map(mapId).filter(Boolean), session:resumed, notice, migrated:true};
}
function load() {
  try {
    const raw = localStorage.getItem(STORE);
    if (!raw) {
      if (localStorage.getItem("aws-security-lab-v4-simple") || localStorage.getItem("aws-security-lab-v3-knowledge")) {
        $("revision-notice").textContent = "המאגר הוחלף לשאלות על מושגים ותפקידים. ההתקדמות החדשה מתחילה מאפס כדי לא לסמן ידע בשאלות אחרות כאילו כבר נלמד. השמירות הישנות נשארו בדפדפן ללא שינוי.";
        $("revision-notice").classList.remove("hidden");
      }
      return;
    }
    const saved = JSON.parse(raw);
    const result = readSave(saved);
    progress = result.progress;
    archivedProgress = result.archivedProgress;
    completedQuestionIds = new Set(result.completedQuestionIds);
    storedSession = result.session;
    if (result.migrated) {
      const backupKey = STORE + "-backup-" + saved.sourceHash;
      if (localStorage.getItem(backupKey) === null) localStorage.setItem(backupKey, raw);
      $("revision-notice").textContent = result.notice + " עותק השמירה המקורית נשמר בנפרד בדפדפן.";
      $("revision-notice").classList.remove("hidden");
      save();
    }
  } catch (error) {
    reportStorage("לא ניתן לקרוא את השמירה. נתונים קיימים לא יידרסו. ניתן להמשיך ללא שמירה: " + error.message);
  }
}
function save() {
  if (storageFailed) return;
  try {
    localStorage.setItem(STORE, JSON.stringify({ version: 5, sourceHash: DATA.meta.hash, progress, archivedProgress, completedQuestionIds:[...completedQuestionIds], session: storedSession }));
  } catch (error) {
    reportStorage("ההתקדמות נשארת בזיכרון אך לא נשמרת בדפדפן. מומלץ לייצא גיבוי: " + error.message);
  }
}
function getProgress(id) {
  return progress[id] || { attempts: 0, successes: 0, streak: 0, last: 0, starred: false, updated: "" };
}
function saveSession() {
  storedSession = session ? JSON.parse(JSON.stringify(session)) : null;
  save();
}
function updateStats() {
  $("source-total").textContent = DATA.meta.recallCards.toLocaleString("he");
  $("source-seen").textContent = recallDeck().filter(unit => getProgress(unit.id).attempts > 0).length.toLocaleString("he");
  $("quiz-total").textContent = (DATA.questions.length + DATA.legacy.length).toLocaleString("he");
  $("image-total").textContent = DATA.meta.images;
  $("resume").classList.toggle("hidden", !storedSession);
  $("bank-progress").textContent = `${DATA.questions.length.toLocaleString("he")} שאלות בסדר קבוע · מספרים 1–${DATA.meta.numberedSlots.toLocaleString("he")}`;
  $("jump-number").max = DATA.meta.numberedSlots;
}
function jumpToQuestion() {
  const number = Number($("jump-number").value);
  if (!Number.isInteger(number) || number < 1 || number > DATA.meta.numberedSlots) {
    $("jump-status").textContent = `יש להזין מספר שאלה שלם בין 1 ל-${DATA.meta.numberedSlots}.`;
    return;
  }
  const deck = modeDeck().filter(unit => unit.number >= number);
  if (!deck.length) {
    $("jump-status").textContent = "השאלה הוסרה ואין שאלות נוספות אחריה.";
    return;
  }
  $("jump-status").textContent = deck[0].number === number ? "" :
    `שאלה ${number} הוסרה. ממשיכים לשאלה ${deck[0].number}; המספרים האחרים לא השתנו.`;
  begin(deck.map(unit => unit.id));
}
function recallDeck() {
  return DATA.cards;
}
function modeDeck() {
  if ($("mode").value === "depth") return DATA.questions;
  return recallDeck();
}
function fillServices() {
  const previous = $("service").value;
  $("service").replaceChildren(new Option("כל השירותים והפרקים", "all"));
  [...new Set(modeDeck().map(unit => unit.service))].forEach(service => $("service").add(new Option(service, service)));
  if ([...$("service").options].some(option => option.value === previous)) $("service").value = previous;
  updateSelection();
}
function searchText(unit) {
  return [unit.service, unit.section, unit.topic, unit.text, unit.question || ""].join(" ").toLocaleLowerCase();
}
function filterDeck() {
  const query = $("search").value.trim().toLocaleLowerCase();
  return modeDeck().filter(unit => {
    return ($("service").value === "all" || unit.service === $("service").value) &&
      (!query || searchText(unit).includes(query));
  });
}
function updateSelection() {
  const count = filterDeck().length;
  $("selection-info").textContent = `${count.toLocaleString("he")} שאלות זמינות בנושא שבחרת.`;
  $("mode-explanation").textContent = modes[$("mode").value];
  $("start").disabled = count === 0;
}
function openTab(tab) {
  ["play", "library", "coverage", "guide"].forEach(name => {
    $("view-" + name).classList.toggle("hidden", name !== tab);
    $("tab-" + name).setAttribute("aria-selected", String(name === tab));
  });
  if (tab === "library") renderLibrary();
  if (tab === "coverage") renderCoverage();
}
function begin(ids) {
  let deck = ids ? ids.filter(id => playableIds.has(id)).map(id => byId.get(id)) : filterDeck();
  if (!deck.length) {
    $("selection-info").textContent = "אין פריטים לסבב. יש לשנות את המסננים.";
    return;
  }
  deck = [...deck].sort((a, b) => a.number - b.number);
  if (!ids) {
    const size = $("length").value === "all" ? deck.length : Number($("length").value);
    deck = deck.slice(0, size);
  }
  session = { ids: deck.map(unit => unit.id), index: 0, score: 0, missed: [], answered: false, revealed: false, choice: "", note: "" };
  $("setup").classList.add("hidden");
  $("results").classList.add("hidden");
  $("game").classList.remove("hidden");
  openTab("play");
  saveSession();
  render();
}
function current() { return byId.get(session.ids[session.index]); }
function addImage(parent, unit, lazy = false) {
  const image = node("img", undefined, "source-image");
  image.src = unit.image;
  image.alt = `תמונת מקור: ${unit.service} — ${unit.topic}; מקטע ${unit.blocks[0] + 1}`;
  image.loading = lazy ? "lazy" : "eager";
  const button = node("button");
  button.type = "button";
  button.setAttribute("aria-label", "הגדלת תמונת המקור");
  button.style.cssText = "display:block;width:100%;padding:0;border:0;background:transparent";
  button.append(image);
  button.addEventListener("click", () => openSource(unit.id));
  parent.append(button);
}
function corrections(unit) {
  const text = unit.text || "";
  const notes = [];
  if (/Permissions Boundaries|Permissions Boundary/.test(unit.topic) || /Permission Boundaries/.test(text)) notes.push("דיוק: Permissions Boundaries מוצמדים ל-IAM Users או Roles, לא ל-Groups ולא ל-root. הם אינם מעניקים הרשאות בפני עצמם.");
  if (unit.service === "AWS IAM" && /External ID|סיסמת הביטחון|סיסמת בטיחות/.test(text)) notes.push("דיוק: External ID אינו סיסמה ואינו נחשב סוד. הספק צריך לייצר ערך ייחודי לכל לקוח ולדרוש אותו בתנאי Trust Policy כדי לצמצם Confused Deputy.");
  if (unit.service === "AWS IAM" && /Role|role/.test(unit.topic + text) && /קבוצות|קבוצה/.test(text)) notes.push("תיקון: IAM Group אינו Principal ואינו יכול לבצע AssumeRole. אפשר לתת לחברי קבוצה הרשאת AssumeRole באמצעות מדיניות זהות, אבל לא להצמיד את הקבוצה עצמה כ-Principal ב-Trust Policy.");
  if (unit.service === "AWS IAM Identity Center" && /מאבדים את היכולת|איבדנו את היכולת/.test(text)) notes.push("דיוק: מינוי Delegated Administrator אינו מבטל Permission Sets בחשבון ה-Management. ניהול הגישה לחשבון הזה נשאר באחריות חשבון ה-Management, ולא עובר למנהל המואצל.");
  if (unit.service === "AWS IAM" && /Internal Access|Custom Policy Checks/.test(unit.topic + text)) notes.push("דיוק: Internal Access Analysis ו-Custom Policy Checks הם מנגנונים שונים. השאלה כאן עוסקת בבדיקת מדיניות לפני השימוש בה, כפי שמתואר בציטוט, ולא במיפוי הגישה הפנימית.");
  if (unit.service === "AWS WAF" && /החוק האחרון יהיה allow all/.test(text)) notes.push("דיוק: פעולת ברירת המחדל של Web ACL יכולה להיות Allow או Block. אין חובה שהחוק האחרון יאשר את כל הבקשות.");
  if (unit.service === "AWS Transit Gateway" && /טבלת ניתוב מרכזית אחת גלובלית/.test(text)) notes.push("דיוק לציטוט: אפשר להגדיר כמה טבלאות ניתוב ב-Transit Gateway ולשייך אליהן חיבורים שונים. אין חובה שכל ה-VPCs ישתמשו בטבלה אחת, והטבלאות אינן גלובליות בין כל האזורים.");
  if (unit.service === "AWS" && /תוכניות בתשלום|מהנדס ייעודי/.test(text)) notes.push("דיוק לציטוט: זמני התגובה וליווי TAM תלויים בתוכנית התמיכה ובחומרת הפנייה. אין להסיק שכל תוכנית בתשלום כוללת TAM או מענה בתוך דקות לכל פנייה.");
  if (unit.service === "S3" && /לא רלוונטי|לא ל-objects/.test(text)) notes.push("תיקון: Bucket Policy רלוונטית גם בתוך Organizations, ויכולה לתת או להגביל גישה גם לאובייקטים לפי Action ו-Resource.");
  if (unit.service === "S3" && /גרסאות קודמות של ה-bucket/.test(text)) notes.push("דיוק: Versioning שומר גרסאות אובייקטים, לא גרסאות של ה-Bucket עצמו.");
  if (unit.service === "S3" && /Signed URLs|Intelligent Tiering|9 עד 48/.test(text)) notes.push("בדיקת עדכניות: זמני אחזור ותכונות שכבות האחסון דורשים דיוק. Intelligent-Tiering מבוסס על ניטור דפוסי גישה; אין להסיק שעצם שינוי השכבה משנה את תוכן הנתון.");
  if (unit.service === "AWS KMS" && /rotation|Rotation|מחיקה של מפתח|ScheduleKey/.test(text)) notes.push("דיוק ועדכניות: הפעולה לתזמון מחיקה היא ScheduleKeyDeletion, ולביטול CancelKeyDeletion. תמיכת רוטציה תלויה בסוג המפתח ובמקור חומר המפתח; יש לבדוק תיעוד עדכני ולא להסתמך על רשימה היסטורית.");
  if (unit.service === "AWS KMS" && /Key Policy/.test(text)) notes.push("דיוק: גם AWS Managed Keys כוללים Key Policy שאי אפשר לשנות. ב-Customer Managed Keys הלקוח מנהל את המדיניות.");
  if (unit.service === "AWS KMS" && /Multi-Region KMS Key|עותקי Replica/.test(unit.topic + text)) notes.push("תיקון לציטוט: ניתן לתזמן מחיקה של Primary Key גם כשקיימים Replicas. המחיקה בפועל ותחילת תקופת ההמתנה של ה-Primary תלויות במחיקת כל ה-Replicas. לכן הטענה שחסום לחלוטין עצם התזמון אינה מדויקת.");
  if (unit.service === "VPC" && /מחקת Custom NACL|ניתקת סאבנט/.test(text)) notes.push("דיוק: כל Subnet חייב להיות משויך ל-NACL אחד. אין להשאירו מנותק; משנים את השיוך ל-NACL אחר. אי אפשר למחוק Custom NACL כל עוד הוא משויך ל-Subnet. ללא שיוך מפורש משתמשים ב-Default NACL.");
  if (unit.service === "VPC" && /NAT Gateway/.test(unit.topic + text)) notes.push("דיוק: יציאה לאינטרנט דרך Internet Gateway מתייחסת ל-Public NAT Gateway. Private NAT Gateway משמש לתקשורת פרטית, ולא ליציאה לאינטרנט דרך IGW.");
  if (unit.service === "AWS Systems Manager" && /Session manager history/.test(unit.topic + text)) notes.push("דיוק: היסטוריית Session Manager מתייחסת לסשנים שנוהלו דרכו, לא לכל סוגי ההתחברות האפשריים בארגון.");
  if (unit.service === "VPC" && /Hyperplane|backbone|תרגום של דומיניים/.test(text)) notes.push("תיקון מהותי: NAT אינו DNS. Hyperplane הוא מישור עיבוד תעבורת רשת מבוזר; PrivateLink אינו כל רשת ה-backbone של AWS. אין ללמוד מהציטוט שכל שירותי PrivateLink מחייבים שהלקוח יקים Load Balancer.");
  if (unit.service === "EC2" && /יבקש את המפתח הפרטי/.test(text)) notes.push("תיקון: בחיבור SSH הלקוח מוכיח החזקה במפתח הפרטי; אין למסור או להעלות את המפתח הפרטי לשרת.");
  if (unit.service === "AWS Backup" && /Vault Lock|בלתי ניתנים לשינוי או מחיקה/.test(unit.topic + text)) notes.push("דיוק: ההגנה של Vault Lock תלויה במצב הנעילה. במצב Governance אפשר לבטל אותה עם הרשאה מתאימה; במצב Compliance אי אפשר לבטל אותה לאחר סיום תקופת ההמתנה. לכן לא כל נעילה מונעת מחיקה גם ממנהל.");
  if (unit.service === "CloudTrail" && /הופרש|retired|Lake/.test(text)) notes.push("בדיקת עדכניות: טענת המקור לגבי פרישת CloudTrail Lake אינה מאומתת כאן. יש לבדוק את הודעות AWS הרשמיות; אין להשתמש בטענה זו כעובדה לבחינה.");
  if (unit.service === "S3" && /Replication|רפליקציה|replication/.test(unit.topic + text)) notes.push("דיוק: S3 Replication הוא אסינכרוני ואינו מבטיח תמיד העתקה תוך שניות או דקות. תנאי שכפול אובייקטים קיימים ותמיכת סוגי הצפנה דורשים בדיקה נפרדת.");
  if (unit.service === "S3" && /יחלץ את מפתחות|מוטמעים בתוכו/.test(text)) notes.push("תיקון: Presigned URL אינו מכיל את ה-Secret Access Key ואינו מאפשר לחלץ אותו מהחתימה. הוא מכיל מזהה מפתח, חתימה ולעיתים Session Token. תוקף הקישור מוגבל גם בתוקף פרטי הגישה ששימשו לחתימה.");
  if (unit.service === "Amazon Route 53" && /אינטגרצי|אף שירות/.test(text)) notes.push("תיקון: Route 53 אינו מוגבל לאינטגרציה עם CloudWatch בלבד; קיימים שילובים עם שירותים נוספים. יש לבחון בנפרד סוג לוג ויעד נתמך.");
  if (unit.service === "ECR" && /Basic Scanning|באופן ידני/.test(text)) notes.push("דיוק: Basic Scanning תומך גם ב-scan-on-push. Enhanced Scanning משתלב עם Amazon Inspector ומכסה גם חבילות של שפות תכנות.");
  if (unit.service === "GuardDuty" && /default|כל מקורות|5 שירותי/.test(text)) notes.push("עדכניות: יש להבחין בין מקורות הבסיס לבין תוכניות הגנה נוספות ובין Auto-enable ארגוני להפעלה באזור. רשימת מקורות ומצבי ברירת מחדל עשויים להשתנות.");
  if (unit.service === "GuardDuty" && /מפסיק לייצר|חוסם את יצירת/.test(text)) notes.push("דיוק: רשימות אמון אינן חומת אש וההשפעה שלהן תלויה בסוג הממצא ותוכנית ההגנה. אין להניח שהן משתיקות כל סוג ממצא.");
  if (unit.service === "Amazon EKS" && /IRSA|IMDS/.test(text)) notes.push("דיוק: IRSA לבדו אינו חוסם גישה ל-IMDS. יש להגדיר גם בידוד גישה למטא-נתונים כדי לצמצם שימוש בהרשאות ה-Node.");
  if (unit.service === "CloudFront" && /Signed URL|שבוע ימים|ללא הגבלת זמן/.test(text)) notes.push("דיוק: Signed URLs של CloudFront כוללים תנאי תפוגה. S3 Presigned URL מוגבל גם לאורך חיי ה-credentials, ולעיתים אפשר להגביל אותו במדיניות מעבר לזמן בלבד.");
  if (unit.service === "CloudFront" && /MediaStore/.test(text)) notes.push("בדיקת עדכניות: רשימת ה-origins הנתמכים ומצב שירותי מדיה השתנו עם השנים. יש לבדוק תמיכת OAC לכל סוג מקור; קיום OAC אינו חוסם מקור ללא Policy מתאימה.");
  if (unit.service === "AWS Elastic Load Balancing" && /מחייבים.*2|רק בתעודות/.test(text)) notes.push("דיוק: דרישות Subnets ו-AZs תלויות בסוג ה-Load Balancer. לא כל מה שנכון ל-ALB חל גם על NLB או GWLB.");
  if (unit.service === "AWS Elastic Load Balancing" && /SSL Termination|Server Name Indication|ELB Access Logs/.test(unit.topic + text)) notes.push("דיוק: TLS Termination אינו מחייב חיבור HTTP לא מוצפן ליעד. SNI מסייע בבחירת תעודה לפי שם המארח. יכולות, שכבת הלוגים ופרוטוקולים נתמכים משתנים בין ALB, NLB ו-GWLB.");
  if (unit.service === "Amazon API Gateway" && /Cognito|Execution Logs|WAF/.test(unit.topic + text)) notes.push("דיוק: אינטגרציות ולוגים נתמכים משתנים לפי סוג ה-API. Cognito Authorizer מאמת טוקנים; אין להסיק ש-API Gateway שולח שם משתמש וסיסמה ל-Cognito בכל בקשה. אינטגרציית WAF הישירה רלוונטית ל-REST APIs נתמכים.");
  if (unit.service === "AWS Systems Manager" && /בחינם|CloudTrail.*מדדים/.test(text)) notes.push("דיוק: Parameter Store Standard ו-Advanced שונים בתמחור וביכולות. CloudWatch הוא מקור מדדים; CloudTrail הוא תיעוד פעולות API.");
  if (unit.service === "Athena" && /חיוב|שאילתות/.test(text)) notes.push("דיוק: בחיוב Athena לפי סריקה, התשלום מבוסס על נפח הנתונים שנסרק, לא רק על מספר השאילתות. קיימות גם אפשרויות קיבולת ותמחור נוספות.");
  if (unit.service === "CloudWatch" && /מרכז trails/.test(text)) notes.push("דיוק: Log Group מאגדת Log Streams. היא יכולה להכיל לוגים ממקורות רבים, ולא רק CloudTrail.");
  if (unit.service === "CloudWatch" && /חבילה של מדדים/.test(text)) notes.push("דיוק: Metric Stream מזרים נתוני מדדים באופן רציף ליעדים נתמכים; הוא אינו רק מיכל ארגוני כמו Namespace.");
  if (unit.service === "VPC" && /6 subnets/.test(text)) notes.push("עדכניות: מספר ה-Subnets ב-Default VPC תלוי ב-Availability Zones באזור; אין ללמוד שמדובר תמיד בשישה.");
  if (unit.service === "Amazon Inspector") notes.push("עדכניות: Assessment Templates הם מושג של Inspector Classic. Inspector הנוכחי תומך בשיטות סריקה שונות, כולל מצבים ללא Agent; אין להשליך את מודל Classic על השירות הנוכחי.");
  if (unit.service === "Amazon Kinesis Data Streams" && /צריך להשתמש.*Firehose/.test(text)) notes.push("תיקון: Firehose אינו חובה. אפשר להשתמש ב-consumers אחרים, למשל KCL או אינטגרציות נתמכות.");
  if (/Other Services/.test(unit.service) && /אין downtime|מערכת טיקטים|הופרש|לא קיים יותר|Snowmobile|SMS|CodeStar|OpsWorks|Cloud9/.test(text)) notes.push("בדיקת דיוק ועדכניות: אין הבטחה לאפס downtime בכל תרחיש הגירה; Connect הוא Contact Center; טענות על פרישת שירותים וזמינות דורשות בדיקה פרטנית מול AWS.");
  return notes;
}
function renderNotes(parent, unit) {
  corrections(unit).forEach(text => parent.append(node("p", text, "warning")));
}
function sourceActions(unit) {
  const actions = $("source-actions");
  actions.replaceChildren();
  const sourceId = unit.id;
  if (sourceId) {
    const button = node("button", "פתיחת קטע המקור");
    button.type = "button";
    button.addEventListener("click", () => openSource(sourceId));
    actions.append(button);
  } else {
    actions.append(node("p", "פריט מהמאגר הישן — ללא שיוך מאומת למקטע בגרסה המעודכנת.", "muted tiny"));
  }
}
function render() {
  const unit = current();
  const objective = ["quiz", "legacy"].includes(unit.kind);
  const percent = Math.round(session.index / session.ids.length * 100);
  $("round-progress").textContent = `שאלה ${unit.number} מתוך ${DATA.meta.numberedSlots} · בסבב ${session.index + 1} מתוך ${session.ids.length}`;
  $("round-score").textContent = `הצלחות בסבב: ${session.score}`;
  $("round-bar").style.width = percent + "%";
  document.querySelector('[role="progressbar"]').setAttribute("aria-valuenow", percent);
  $("context").textContent = objective ? unit.section : [unit.service, unit.section, unit.topic].filter((part, i, parts) => part && parts.indexOf(part) === i).join(" ← ");
  $("kind-label").textContent = objective ? "היכרות עם מושג ותפקידו" : "שליפת הגדרה · הערכה עצמית";
  $("question").textContent = unit.question;
  $("visual").replaceChildren();
  if (unit.kind === "image") addImage($("visual"), unit);
  $("source-cue").classList.add("hidden");
  $("recall").classList.toggle("hidden", objective);
  $("notes").value = session.note;
  $("reveal").disabled = session.revealed;
  $("notes").disabled = session.answered;
  $("answer-panel").classList.add("hidden");
  $("grade").classList.add("hidden");
  $("next").classList.toggle("hidden", !session.answered);
  $("next").textContent = session.index + 1 === session.ids.length ? "לסיום הסבב" : "לשאלה הבאה";
  $("options").replaceChildren();
  $("unknown-answer").classList.toggle("hidden", !objective);
  $("unknown-answer").disabled = session.answered;
  $("grading-note").textContent = objective ? "ציון לפי התשובה הנכונה." : "הערכה עצמית של ההגדרה והתפקיד; לא שלבי יישום.";
  if (objective) {
    if (unit.kind === "quiz") {
      const offset = unit.number % unit.options.length;
      quizOptions = [...unit.options.slice(offset), ...unit.options.slice(0, offset)];
    }
    else {
      const terms = [...new Map(DATA.legacy
        .filter(item => item.id !== unit.id && canonicalTerm(item.answer) !== canonicalTerm(unit.answer) && item.service === unit.service)
        .map(item => [canonicalTerm(item.answer), item.answer])).values()];
      quizOptions = shuffle([unit.answer, ...shuffle(terms).slice(0,3)]);
    }
    quizOptions.forEach((text, i) => {
      const button = node("button", undefined, "option");
      button.append(node("span", String(i + 1), "option-number"), node("span", text));
      button.type = "button";
      button.dataset.answer = text;
      button.addEventListener("click", () => answerQuiz(text));
      $("options").append(button);
    });
  }
  updateStar();
  if (session.revealed || session.answered) showAnswer();
  $("question").focus({preventScroll:true});
}
function showAnswer() {
  const unit = current();
  const objective = ["quiz","legacy"].includes(unit.kind);
  $("answer-panel").classList.remove("hidden");
  $("feedback").className = "feedback";
  if (objective) {
    const correct = session.choice === unit.answer;
    $("feedback").classList.add(correct ? "good" : "bad");
    $("feedback").textContent = `${correct ? "נכון." : session.choice === UNKNOWN_ANSWER ? "לא ידעת? המושג נוסף לחזרה." : "לא הפעם."} התשובה: ${unit.answer}\n\n${unit.text}`;
    $("unknown-answer").disabled = true;
    [...$("options").children].forEach(button => {
      button.disabled = true;
      if (button.dataset.answer === unit.answer) button.classList.add("correct");
      else if (button.dataset.answer === session.choice) button.classList.add("wrong");
    });
  } else {
    $("feedback").textContent = `הגדרה ללמידה: ${unit.answer}\n\nהשווה להסבר שנתת, ואז הערך את השליפה. הציטוט המקורי מופיע בנפרד.`;
    $("grade").classList.toggle("hidden", session.answered);
    $("reveal").disabled = true;
  }
  $("source-notes").replaceChildren();
  const source = unit.source ? byId.get(unit.source) : unit;
  const quote = node("section", undefined, "library-card");
  quote.append(node("h3", unit.imageQuoteType === "label" ? "ציטוט שם השירות מתוך התרשים" : unit.imageQuote ? "ציטוט מתוך תרשים המקור (תמלול)" : "ציטוט מדויק מתוך הסיכום"));
  if (unit.imageQuote) {
    quote.append(node("blockquote", unit.imageQuote, "source-text"));
    const drawing = node("details");
    drawing.append(node("summary", "לראות את תרשים המקור"));
    drawing.addEventListener("toggle", () => {
      if (drawing.open && !drawing.querySelector("img")) addImage(drawing, source, true);
    });
    quote.append(drawing);
  } else if (unit.quoteText) {
    quote.append(sourceText(unit.quoteText, unit.quoteBlocks));
  } else if (objective || source.kind === "text") {
    const sources = unit.quoteSources ? unit.quoteSources.map(id => byId.get(id)) : [source];
    quote.append(sourceText(sources.map(part => part.text).join("\n"), sources.flatMap(part => part.blocks)));
  } else if (source.text) {
    quote.append(node("p", "ציטוט הטקסט הסמוך לתמונה, לא תמלול התרשים:", "muted tiny"));
    quote.append(sourceText(source.text, source.quoteBlocks));
  } else {
    quote.append(node("p", "המקור לפריט זה הוא התרשים המוצג, ללא טקסט הסבר סמוך.", "muted"));
  }
  const quoteBlocks = unit.quoteBlocks || unit.blocks || source.blocks;
  quote.append(node("p", `מקור: ${source.service} · מקטעים ${quoteBlocks.map(n => n + 1).join(", ")}${source.imageNumber ? " · תמונה " + source.imageNumber : ""}`, "muted tiny"));
  $("source-notes").append(quote);
  (unit.evidence || []).forEach(evidence => {
    const detail = node("details");
    detail.append(node("summary", `ציטוט נוסף מתרשים ${evidence.imageNumber}`));
    detail.addEventListener("toggle", () => {
      if (!detail.open || detail.dataset.loaded) return;
      detail.dataset.loaded = "true";
      detail.append(node("blockquote", evidence.imageQuote, "source-text"));
      if (evidence.imageQuoteType === "label") detail.append(node("p", "בתרשים מופיע שם השירות בלבד. ההגדרה בתשובה מבוססת על הטקסט המצוטט מהסיכום.", "notice"));
      addImage(detail, byId.get(evidence.source), true);
    });
    $("source-notes").append(detail);
  });
  const noteSources = unit.quoteSources ? unit.quoteSources.map(id => byId.get(id)) : [{...source, text: unit.quoteText || source.text}];
  [...new Set(noteSources.flatMap(corrections))].forEach(text => $("source-notes").append(node("p", text, "warning")));
  sourceActions(unit);
}
function record(grade) {
  if (session.answered) return;
  const unit = current();
  const p = {...getProgress(unit.id)};
  p.attempts++;
  p.last = grade;
  p.successes += grade === 2 ? 1 : 0;
  p.streak = grade === 2 ? p.streak + 1 : 0;
  p.updated = new Date().toISOString();
  progress[unit.id] = p;
  session.answered = true;
  if (grade === 2) session.score++;
  else session.missed.push(unit.id);
  $("grade").classList.add("hidden");
  $("notes").disabled = true;
  $("next").classList.remove("hidden");
  $("round-score").textContent = `הצלחות בסבב: ${session.score}`;
  saveSession();
  updateStats();
  $("next").focus({preventScroll:true});
}
function answerQuiz(choice) {
  if (session.answered) return;
  session.choice = choice;
  session.revealed = true;
  record(choice === current().answer ? 2 : 0);
  showAnswer();
}
function next() {
  if (!session || !session.answered) return;
  if (session.index + 1 === session.ids.length) {
    $("game").classList.add("hidden");
    $("results").classList.remove("hidden");
    $("result-score").textContent = `${session.score} / ${session.ids.length}`;
    const self = session.ids.filter(id => ["text","image"].includes(byId.get(id).kind)).length;
    $("result-detail").textContent = self ? `${self} פריטי שליפה עם הערכה עצמית. יש ${session.missed.length} פריטים לחזרה ממוקדת.` : `${session.ids.length} שאלות שנבדקו מול תשובה מוגדרת. יש ${session.missed.length} פריטים לחזרה.`;
    storedSession = null;
    save();
    updateStats();
    $("new-round").focus();
    return;
  }
  session.index++;
  Object.assign(session, {answered:false, revealed:false, choice:"", note:""});
  saveSession();
  render();
}
function updateStar() {
  const starred = getProgress(current().id).starred;
  $("star").textContent = starred ? "★ מסומן לחזרה" : "☆ לסמן לחזרה";
  $("star").classList.toggle("bookmark", starred);
  $("star").setAttribute("aria-pressed", starred);
}
function openSource(id) {
  const unit = byId.get(id);
  const source = unit.source ? byId.get(unit.source) : unit;
  $("dialog-title").textContent = `${unit.service} · ${unit.topic}`;
  const body = $("dialog-body");
  body.replaceChildren();
  if (source.image) {
    const img = node("img");
    img.src = source.image;
    img.alt = `תמונת מקור: ${source.topic}`;
    body.append(img);
  }
  body.append(sourceText(unit.quoteText || unit.imageQuote || unit.text,
    unit.imageQuote ? [] : unit.quoteBlocks || unit.blocks || source.blocks, "div"));
  renderNotes(body, {...source, text: unit.quoteText || unit.text});
  const blocks = unit.quoteBlocks || unit.blocks || source.blocks;
  body.append(node("p", "מקטעי מקור: " + blocks.map(n => n+1).join(", "), "muted tiny"));
  if (!$("source-dialog").open) $("source-dialog").showModal();
}
function renderLibrary() {
  const query = $("library-search").value.trim().toLocaleLowerCase();
  const items = DATA.units.filter(unit => !query || searchText(unit).includes(query));
  const pages = Math.max(1, Math.ceil(items.length / 20));
  libraryPage = Math.min(libraryPage, pages - 1);
  $("library-count").textContent = `${items.length.toLocaleString("he")} יחידות מקור מתאימות.`;
  $("library-page").textContent = `${libraryPage + 1} / ${pages}`;
  $("library-prev").disabled = libraryPage === 0;
  $("library-next").disabled = libraryPage + 1 >= pages;
  $("library-items").replaceChildren();
  items.slice(libraryPage*20,libraryPage*20+20).forEach(unit => {
    const item = node("details", undefined, "library-card");
    item.append(node("summary", `${unit.service} · ${unit.topic} · ${unit.kind === "image" ? "תמונה" : "טקסט"} · ${unit.blocks.map(n => n+1).join(", ")}`));
    let loaded = false;
    item.addEventListener("toggle", () => {
      if (!item.open || loaded) return;
      loaded = true;
      if (unit.image) addImage(item, unit, true);
      item.append(sourceText(unit.text, unit.quoteBlocks || unit.blocks, "div"));
      renderNotes(item, unit);
      item.append(node("p", `סיווג: ${unit.classification === "knowledge" ? "ידע להבנה ולתרגול" : unit.classification === "implementation" ? "יישום / How-to — לא נכלל במשחק" : "כותרת / הקשר — לא שאלה נפרדת"}`, "notice"));
      if (unit.concepts.length) {
        const button = node("button", "לתרגול המושגים הקשורים");
        button.type = "button";
        button.addEventListener("click", () => begin(unit.concepts));
        item.append(button);
      }
    });
    $("library-items").append(item);
  });
}
function renderCoverage() {
  $("audit-summary").textContent = `${DATA.meta.textBlocks.toLocaleString("he")} מקטעי טקסט ו-${DATA.meta.images} תמונות נשמרו במלואם לעיון. במשחק ${DATA.questions.length} מושגים של AWS, עם ארבע תשובות ואפשרות ״לא יודע את התשובה״. לכל שם מושג יש אסמכתה במקור. תוכן תחת Interface וכל תתי-הכותרות שלו מוחרג מהשאלון, גם כשמופיעה בו הגדרה. הוחרגו גם מושגי יסוד כלליים והוראות יישום. כל ${DATA.meta.sourceOnlyReview.reviewed} השאלות הקודמות נבדקו; ${DATA.meta.sourceOnlyReview.corrected} הגדרות תוקנו ו-${DATA.meta.sourceOnlyReview.removed} שאלות הוסרו. כרטיסיות השליפה מתרגלות את אותו מאגר, ואינן מושגים נוספים.`;
  $("source-meta").textContent = `מקור: ${DATA.meta.source}\nמועד הורדה (UTC): ${DATA.meta.fetched}\nSHA-256: ${DATA.meta.hash}`;
  $("coverage-items").replaceChildren();
  DATA.meta.services.forEach(service => {
    const units = recallDeck().filter(unit => unit.service === service);
    const seen = units.filter(unit => getProgress(unit.id).attempts).length;
    const stable = units.filter(unit => getProgress(unit.id).streak >= 2).length;
    const questions = DATA.questions.filter(unit => unit.service === service);
    const correct = questions.filter(unit => getProgress(unit.id).streak >= 2).length;
    const row = node("div", undefined, "coverage-row");
    row.append(node("strong", service));
    const quizSeen = questions.filter(unit => getProgress(unit.id).attempts).length;
    row.append(node("p", `${quizSeen}/${questions.length} מושגים תורגלו בשאלון · ${correct} עם שתי הצלחות רצופות · בשליפה עצמית: ${seen} תורגלו, ${stable} יציבים`, "muted tiny"));
    const bar = node("div", undefined, "progress");
    bar.setAttribute("role","progressbar");
    bar.setAttribute("aria-label",`פריטי מקור שתורגלו: ${service}`);
    bar.setAttribute("aria-valuemin","0");
    bar.setAttribute("aria-valuemax",String(questions.length));
    bar.setAttribute("aria-valuenow",String(quizSeen));
    const value = node("span");
    value.style.width = (questions.length ? quizSeen / questions.length * 100 : 0) + "%";
    bar.append(value);
    row.append(bar);
    const button = node("button", "לתרגול הפרק");
    button.type = "button";
    button.addEventListener("click", () => {
      $("mode").value = questions.length ? "depth" : "source";
      fillServices();
      $("service").value = service;
      $("search").value = "";
      updateSelection();
      openTab("play");
      $("setup").classList.remove("hidden");
      $("game").classList.add("hidden");
      $("results").classList.add("hidden");
    });
    row.append(button);
    $("coverage-items").append(row);
  });
}
function exportProgress() {
  const blob = new Blob([JSON.stringify({version:5,sourceHash:DATA.meta.hash,progress,archivedProgress,completedQuestionIds:[...completedQuestionIds],session:storedSession},null,2)], {type:"application/json"});
  const url = URL.createObjectURL(blob);
  const link = node("a");
  link.href = url;
  link.download = "aws-security-progress.json";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  $("backup-status").textContent = "נוצר קובץ גיבוי להורדה. שמור אותו במקום בטוח.";
}
$("import").addEventListener("change", async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error("קובץ הגיבוי גדול מדי.");
    const saved = JSON.parse(await file.text());
    const result = readSave(saved);
    if (!confirm((result.notice ? result.notice + "\n\n" : "") + "יבוא הגיבוי יחליף את ההתקדמות והסבב השמור בדפדפן זה. להמשיך?")) return;
    progress = result.progress;
    archivedProgress = result.archivedProgress;
    completedQuestionIds = new Set(result.completedQuestionIds);
    storedSession = result.session;
    session = null;
    $("setup").classList.remove("hidden");
    $("game").classList.add("hidden");
    $("results").classList.add("hidden");
    save();
    updateStats();
    renderCoverage();
    updateSelection();
    $("backup-status").textContent = storageFailed ? "הגיבוי נטען לזיכרון בלבד; שמירת הדפדפן אינה זמינה." : "הגיבוי נטען ונשמר. " + result.notice;
  } catch (error) {
    $("backup-status").textContent = "לא בוצע יבוא: " + error.message;
  } finally { event.target.value = ""; }
});
["play","library","coverage","guide"].forEach(tab => $("tab-"+tab).addEventListener("click", () => openTab(tab)));
$("mode").addEventListener("change", fillServices);
["service","length"].forEach(id => $(id).addEventListener("change", updateSelection));
$("search").addEventListener("input",updateSelection);
$("start").addEventListener("click",() => begin());
$("jump-form").addEventListener("submit", event => { event.preventDefault(); jumpToQuestion(); });
$("reveal").addEventListener("click",() => {
  if (!session || session.revealed) return;
  session.revealed = true;
  session.note = $("notes").value;
  saveSession();
  showAnswer();
  $("grade").querySelector("button").focus({preventScroll:true});
});
$("notes").addEventListener("input", () => { session.note = $("notes").value; saveSession(); });
$("grade").querySelectorAll("button").forEach(button => button.addEventListener("click",() => record(Number(button.dataset.grade))));
$("next").addEventListener("click", next);
$("pause").addEventListener("click",() => {
  if (session) { session.note = $("notes").value; saveSession(); }
  $("game").classList.add("hidden");
  $("setup").classList.remove("hidden");
  updateSelection();
  $("resume").focus();
});
$("resume").addEventListener("click",() => {
  if (!storedSession) return;
  session = JSON.parse(JSON.stringify(storedSession));
  $("mode").value = current().kind === "quiz" ? "depth" : "source";
  session.ids = modeDeck().filter(unit => unit.number >= byId.get(session.ids[session.index]).number).map(unit => unit.id);
  session.index = 0;
  session.score = 0;
  session.missed = [];
  Object.assign(session, {answered:false, revealed:false, choice:"", note:""});
  saveSession();
  $("setup").classList.add("hidden");
  $("results").classList.add("hidden");
  $("game").classList.remove("hidden");
  render();
});
$("star").addEventListener("click",() => {
  const id = current().id;
  progress[id] = {...getProgress(id), starred:!getProgress(id).starred};
  save();
  updateStar();
});
$("unknown-answer").addEventListener("click", () => answerQuiz(UNKNOWN_ANSWER));
$("new-round").addEventListener("click",() => {
  $("results").classList.add("hidden");
  $("setup").classList.remove("hidden");
  updateSelection();
});
$("dialog-close").addEventListener("click",() => $("source-dialog").close());
$("library-search").addEventListener("input",() => {libraryPage=0;renderLibrary();});
$("library-prev").addEventListener("click",() => {libraryPage--;renderLibrary();});
$("library-next").addEventListener("click",() => {libraryPage++;renderLibrary();});
$("export").addEventListener("click",exportProgress);
document.addEventListener("keydown",event => {
  if ($("source-dialog").open || ["INPUT","TEXTAREA","SELECT","BUTTON"].includes(document.activeElement.tagName) || $("game").classList.contains("hidden") || !session) return;
  if (/^[1-4]$/.test(event.key) && !session.answered && ["quiz","legacy"].includes(current().kind)) {
    const choice=quizOptions[Number(event.key)-1];
    if (choice) {event.preventDefault();answerQuiz(choice);}
  }
  if (event.code === "Space" && !session.revealed && ["text","image"].includes(current().kind)) {event.preventDefault();$("reveal").click();}
  if (event.key === "Enter" && session.answered) {event.preventDefault();next();}
});
load();
fillServices();
updateStats();
