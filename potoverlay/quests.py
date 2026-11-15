"""Quest database (per map) and detection of which quests are active.

The game's quest list looks like:

    Quests
    Gather Mushrooms                          <- title (coloured)
    - Collect 15 Milk Cap Mushrooms 0/15      <- objective
    Sand Rings Waystone
    - Restore Waystone at Sand Rings Waystone by
    Delivering Glow Starfish!                 <- objective wrapped onto 2 lines
    1% Complete

Objectives are white and read reliably; titles are coloured and sometimes don't.
So each quest in the list is matched mainly by its objective, with the title as a
tie-breaker. The same quest often exists at many POIs; all of those stay as
candidates and the overlay later picks the one nearest the player.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

Point = tuple[float, float]


@dataclass
class Quest:
    name: str
    # Item spawn points, in full-size map image pixels.
    points: list[Point] = field(default_factory=list)
    # Other text that identifies this quest in the quest list.
    aliases: list[str] = field(default_factory=list)
    color: str = ""
    id: str = ""
    poi: str = ""
    type: str = ""
    # Objective text as the game shows it, e.g. "Collect 15 Milk Cap Mushrooms".
    objectives: list[str] = field(default_factory=list)
    # Places to go (waystone, drop-off, POI), drawn as rings.
    targets: list[Point] = field(default_factory=list)
    center: Point | None = None
    source: str = ""
    # Spawn points per objective ("Collect 15 Death Cap" -> its death caps), so only what's still
    # listed on the HUD gets markers. `points` holds them all.
    objective_points: dict[str, list[Point]] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.id or self.name

    def points_for(self, objective_ids) -> list[Point]:
        """Spawn points of just these objectives (objective_id()s, as the tracker reports them).
        All points if none of them is one of this quest's objectives (e.g. it was found by its
        title) or there's no per-objective data."""
        ids = set(objective_ids or ())
        mine = [o for o in self.objective_points if objective_id(o) in ids]
        if not mine:
            return list(self.points)
        return list(dict.fromkeys(p for o in mine for p in self.objective_points[o]))

    def title_keys(self) -> list[str]:
        return [k for k in (normalize(x) for x in [self.name, *self.aliases]) if k]

    def objective_keys(self) -> list[str]:
        return [k for k in (normalize(x) for x in self.objectives) if k]

    def all_points(self) -> list[Point]:
        return list(self.points) + list(self.targets)


@dataclass
class QuestDB:
    map_name: str
    quests: list[Quest] = field(default_factory=list)
    attribution: str = ""
    # When Potservers last updated the data these quests were built from (ISO time), so the app
    # can tell whether a newer version is out.
    data_updated_at: str = ""

    @classmethod
    def load(cls, path: Path, map_name: str) -> "QuestDB":
        if not path.exists():
            return cls(map_name)
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        quests = []
        for q in raw.get("quests", []):
            quests.append(Quest(
                name=q["name"],
                points=[(float(p[0]), float(p[1])) for p in q.get("points", [])],
                aliases=list(q.get("aliases", [])),
                color=q.get("color", ""),
                id=q.get("id", ""),
                poi=q.get("poi", ""),
                type=q.get("type", ""),
                objectives=list(q.get("objectives", [])),
                targets=[(float(p[0]), float(p[1])) for p in q.get("targets", [])],
                center=tuple(q["center"]) if q.get("center") else None,
                source=q.get("source", ""),
                objective_points={o: [(float(p[0]), float(p[1])) for p in ps]
                                  for o, ps in (q.get("objective_points") or {}).items()},
            ))
        return cls(raw.get("map", map_name), quests, raw.get("attribution", ""), raw.get("data_updated_at", ""))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        out = []
        for q in self.quests:
            d = {"name": q.name}
            if q.id:
                d["id"] = q.id
            if q.poi:
                d["poi"] = q.poi
            if q.type:
                d["type"] = q.type
            if q.objectives:
                d["objectives"] = q.objectives
            d["aliases"] = q.aliases
            d["color"] = q.color
            d["points"] = [[round(x, 1), round(y, 1)] for x, y in q.points]
            if q.targets:
                d["targets"] = [[round(x, 1), round(y, 1)] for x, y in q.targets]
            if q.center:
                d["center"] = [round(q.center[0], 1), round(q.center[1], 1)]
            if q.source:
                d["source"] = q.source
            if len(q.objective_points) > 1:  # only needed when a quest has several items
                d["objective_points"] = {o: [[round(x, 1), round(y, 1)] for x, y in ps]
                                         for o, ps in q.objective_points.items()}
            out.append(d)
        raw = {"map": self.map_name}
        if self.attribution:
            raw["attribution"] = self.attribution
        if self.data_updated_at:
            raw["data_updated_at"] = self.data_updated_at
        raw["quests"] = out
        text = json.dumps(raw, indent=1) + "\n"  # serialise fully before touching the file
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def by_key(self) -> dict[str, Quest]:
        return {q.key: q for q in self.quests}


_strip = re.compile(r"[^a-z0-9 ]+")
_progress = re.compile(r"(\d+)\s*/\s*(\d+)")
_bullet = re.compile(r"^\s*[-–—•=*·«»~]+\s*")
_percent = re.compile(r"\d+\s*%\s*complete", re.I)
_verb = re.compile(r"^.?(collect|deliver|restore waystone)\b")


def normalize(text: str) -> str:
    """Lowercase, drop punctuation and progress counters like '2/5', squash spaces."""
    t = text.lower()
    t = re.sub(r"\d+\s*/\s*-?\s*\d+(\s+\d\b)?", " ", t)  # also OCR splits like "0/1 5", "0/-15"
    t = _strip.sub(" ", t)
    t = re.sub(r"(?<=\d) (?=\d)", "", " ".join(t.split()))  # "collect 1 5 milk" -> "collect 15 milk"
    return t


def is_complete(line: str) -> bool:
    """Whether the counter reads as finished ("15/15"). Informational only: the game removes a
    finished objective from the list, so anything listed still counts as active. (OCR often
    garbles counters, e.g. "3/15" as "3/1 5", which would look finished.)"""
    m = _progress.findall(line)
    if not m:
        return False
    done, total = (int(v) for v in m[-1])
    return total > 0 and done >= total


def match_score(key: str, line: str) -> float:
    """How well a known text matches an OCR line (0..100). The key must be (nearly) contained in the line."""
    if not key or not line:
        return 0.0
    if len(line) >= len(key):
        return max(fuzz.ratio(key, line), fuzz.partial_ratio(key, line))
    # Line shorter than the key: only accept near-identical text (OCR may drop a letter).
    return fuzz.ratio(key, line) if len(line) >= 0.85 * len(key) else 0.0


_obj_count = re.compile(r"^(collect|deliver|gather|kill|hunt)\s+(\d+)\s+(.+)$")
# Same, for OCR'd lines: tolerate a stray character before the verb and a misread letter
# ("jcollect", "cbllect").
_obj_count_ocr = re.compile(r"^\w{0,2}?(c\w{1,2}llect|collect|del\w{0,2}ver|gather|kill|hunt)\s+(\d+)\s+(.+)$")


def _trim_junk(item: str) -> str:
    """Drop leftovers of a misread progress counter after the item: "concho 15" -> "concho",
    "tuktle shell 0 c" -> "tuktle shell"."""
    words = item.split()
    while len(words) > 1 and (any(c.isdigit() for c in words[-1]) or len(words[-1]) <= 2):
        words.pop()
    return " ".join(words)


def _ocr_verb(v: str) -> str:
    return "collect" if v.endswith("llect") else "deliver" if v.startswith("del") else v
_obj_waystone = re.compile(r"^restore waystone at (.+?) by delivering (.+)$")


def objective_score(key: str, line: str) -> float:
    """Compare a known objective with an OCR'd one. The item must match closely; most of the
    wording ("Collect 15 ... Mushrooms") is shared between quests and mustn't carry the score."""
    k, l = _obj_count.match(key), _obj_count_ocr.match(line)
    if k and l:
        if k[1] != _ocr_verb(l[1]):
            return 0.0
        s = max(fuzz.ratio(k[3], l[3]), fuzz.ratio(k[3], _trim_junk(l[3])))
        # Wrong count: still usable (the game's count can differ from the data, and OCR misreads
        # digits), just a slightly worse match so the right count wins when both exist.
        return s - 6 if k[2] != l[2] else s
    if k and not l and len(line) >= len(k[3]):
        # OCR mangled the verb or the count ("—cbllect 15", "collect 158milk cap"): the item name
        # must still be clearly there, and the count can't be trusted.
        return fuzz.partial_ratio(k[3], line) - 8
    k, l = _obj_waystone.match(key), _obj_waystone.match(line)
    if k and l:
        return min(fuzz.ratio(k[1], l[1]), fuzz.ratio(k[2], l[2]))
    if k or l:
        return min(fuzz.ratio(key, line), 80.0)  # different kinds of objective
    return match_score(key, line)


def title_score(key: str, line: str) -> float:
    """A title line is just the title, so compare whole strings ("Cicada" is not "Cicada Molt")."""
    return fuzz.ratio(key, line) if key and line else 0.0


@dataclass
class PanelEntry:
    titles: list[str] = field(default_factory=list)
    objectives: list[str] = field(default_factory=list)
    done: list[bool] = field(default_factory=list)


HOARD_LABEL = "Hoard: Scavenger"
HOARD_KEY = "title:hoard"
# Lines that are never a quest title: the Hoard event's stats and timers, and "Time Left" lines.
_not_title = re.compile(r"^(tier \d|reward|rank\b|score\b|top\b|time left)")
_list_sep = re.compile(r"[,;]|(?<!\d)\s*/\s*(?!\s*\d)")  # item list separators (a "/" not in "3/15")
_verb_any = re.compile(r"^.?.?(c\w{1,2}llect|collect|del\w{0,2}ver|restore|kill|hunt|find|gather|go|visit|reach)\b")
_wraps = re.compile(r"\b(by|at|to|the|and|of|a|an|in|for|from|with|your)$")


def is_hoard_title(text: str) -> bool:
    """The "Hoard: Scavenger" event quest ("Scavenge from the Dead" is a normal quest)."""
    n = normalize(text)
    return "hoard" in n or "scavenger" in n


def parse_panel(lines: list[str]) -> list[PanelEntry]:
    """Group OCR lines into quests: title line(s) followed by '- objective' lines."""
    entries: list[PanelEntry] = []
    cur: PanelEntry | None = None
    for raw in lines:
        line = raw.strip()
        n = normalize(line)
        if not n or fuzz.ratio(n, "quests") >= 80 or _percent.search(line):
            continue
        if not any(c.isalpha() for c in n):
            continue  # timers like "0:56"
        stripped = normalize(_bullet.sub("", line))
        # The Hoard event's item lists ("Bones, Snake Shed, Peeling Bark, ..."; OCR may turn the
        # commas into slashes, or put a stray dash in front), its stats ("Tier 2", "Rank: 6
        # Score: 28", "Top: ...") and "Time Left" lines are neither titles nor objectives.
        if (_list_sep.search(line) and not _verb_any.match(stripped) and not _progress.search(line)) \
                or _not_title.match(stripped):
            continue
        # Objectives start with "- ", but OCR sometimes loses or mangles the dash ("JCollect 5 ...").
        # Titles never have a progress counter, so a line ending in "3/5" is an objective too.
        if _bullet.match(line) or _verb.match(n) or _progress.search(line):
            if cur is None:
                cur = PanelEntry()
                entries.append(cur)
            cur.objectives.append(_bullet.sub("", line))
            cur.done.append(is_complete(line))
            continue
        # A non-bullet line right after an objective that stops mid-sentence ("Restore Waystone at
        # Mire Waystone by" / "Delivering Glow Mushrooms!") is that objective wrapping. Anything
        # else is the next quest's title ("Find Location: Hollow Hills" / "Alpha Critter").
        if cur and cur.objectives and not _progress.search(cur.objectives[-1]) \
                and (_wraps.search(normalize(cur.objectives[-1])) or line[:1].islower()):
            cur.objectives[-1] += " " + line
            cur.done[-1] = cur.done[-1] or is_complete(line)
            continue
        if cur and not cur.objectives:
            cur.titles.append(line)  # title wrapped onto 2 lines, or OCR split it
        else:
            cur = PanelEntry(titles=[line])
            entries.append(cur)
    return entries


def looks_like_quest_list(lines: list[str]) -> bool:
    """An objective line, or the "Quests" header: the quest list is on screen (even if empty)."""
    return any(e.objectives for e in parse_panel(lines)) or any(
        fuzz.ratio(normalize(l), "quests") >= 80 for l in lines)


def _best(scored: list[tuple[float, str]], threshold: float, slack: float = 0.5) -> set[str]:
    """Keys tied for the best score. Ties are the same quest at different POIs (identical text)."""
    if not scored:
        return set()
    top = max(s for s, _ in scored)
    if top < threshold:
        return set()
    return {k for s, k in scored if s >= top - slack}


def objective_id(objective: str) -> str:
    """A stable identity for an objective: verb + item, without the count, e.g. "collect:kelp".
    Taken from the catalog's wording (not the OCR text), so read noise can't change it."""
    n = normalize(objective)
    m = _obj_count.match(n)
    if m:
        return f"{m[1]}:{m[3]}"
    m = _obj_waystone.match(n)
    if m:
        return f"waystone:{m[1]}"
    return f"obj:{n}"


@dataclass
class Detection:
    key: str                       # stable identity (objective_id, or "title:<name>")
    full: frozenset[str]           # candidate catalog quests
    pref: frozenset[str] | None    # those whose name also matches the title above (if readable)
    label: str | None              # the quest's title as shown in game (matched to a catalog name)
    fallback: str                  # name to show if no title was ever read, e.g. "Kelp"


class ActiveQuestTracker:
    """Turns OCR'd quest-list lines into a stable set of active quests.

    Each entry in the game's list is a title with one or more objectives ("Gifts from the Sea":
    collect conch, kelp, turtle shell). Every objective is tracked separately (its item decides the
    markers) under a stable identity from its item, and is labelled with the entry's title, so
    all of a quest's objectives share one name and colour. Labels are decided by majority over
    time, so one misread title can't rename a quest.

    New entries must be seen in 2 of 3 consecutive reads before they count (one garbled read
    can't add a wrong quest); entries are dropped after `miss_tolerance` reads without them."""

    CONFIRM_HITS = 2
    CONFIRM_WINDOW = 3

    def __init__(self, db: QuestDB, threshold: float = 82, miss_tolerance: float = 3):
        self.threshold = threshold
        self.miss_tolerance = miss_tolerance
        self._items: dict[str, dict] = {}    # key -> {"group", "pref", "labels": Counter, "fallback", "misses"}
        self._pending: dict[str, dict] = {}  # key -> {"hits", "age", "det"}
        # What the last update/refresh changed (display names), for the quest log.
        self.added: list[str] = []
        self.dropped: list[str] = []
        self.set_db(db)

    def set_db(self, db: QuestDB) -> None:
        self.db = db
        self._name_of = {q.key: q.name for q in db.quests}
        self._titles = [(q.key, q.title_keys()) for q in db.quests]
        self._names = {}  # normalized catalog name -> name as written
        for q in db.quests:
            for k in q.title_keys():
                self._names.setdefault(k, q.name)
        # Quests without known objective text (e.g. hand-made ones) match objective lines by
        # name/alias contained in the line instead.
        self._objs = [(q.key, q.objective_keys(), q.title_keys()) for q in db.quests]
        self._items.clear()
        self._pending.clear()

    # ---------- results ----------
    def _display(self, item: dict) -> str:
        if item["labels"]:
            return max(item["labels"].items(), key=lambda kv: kv[1])[0]
        return item["fallback"]

    @property
    def labeled(self) -> list[tuple[frozenset[str], str]]:
        """Active objectives as (candidate quest keys, name to show)."""
        return [(it["pref"] or it["group"], self._display(it)) for it in self._items.values()]

    @property
    def labeled_objectives(self) -> list[tuple[frozenset[str], str, str]]:
        """Like `labeled`, plus which objective it is (e.g. "collect:death cap"), so only the items
        listed on the HUD get markers."""
        return [(it["pref"] or it["group"], self._display(it), key) for key, it in self._items.items()]

    @property
    def active(self) -> list[frozenset[str]]:
        return [g for g, _ in self.labeled]

    # ---------- matching ----------
    def _score_titles(self, text: str) -> list[tuple[float, str]]:
        t = normalize(text)
        return [(max((title_score(k, t) for k in keys), default=0.0), key) for key, keys in self._titles]

    def _title_name(self, text: str) -> str | None:
        """The quest name a title line shows: a catalog name if it clearly is one, otherwise the
        text itself if it reads like a clean title (quests missing from the data have titles too)."""
        t = normalize(text)
        if not t:
            return None
        best = max(((title_score(k, t), k) for k in self._names), default=(0.0, ""))
        if best[0] >= self.threshold:
            return self._names[best[1]]
        words = text.strip().split()
        clean = (re.fullmatch(r"[A-Za-z][A-Za-z' ]{3,40}", text.strip()) is not None
                 and all(len(w) >= 2 for w in words) and any(len(w) >= 4 for w in words)
                 and sum(len(w) for w in words) >= 8  # not a fragment like "Ng The"
                 and not looks_like_quest_list([text]) and fuzz.ratio(t, "quests") < 80)
        return " ".join(w[:1].upper() + w[1:].lower() for w in words) if clean else None

    def _score_objective(self, text: str) -> list[tuple[float, str, str]]:
        """(score, quest key, the catalog objective that matched) per catalog quest."""
        o = normalize(text)
        out = []
        for key, objs, names in self._objs:
            if objs:
                s, best = max(((objective_score(k, o), k) for k in objs), key=lambda x: x[0])
            else:
                s, best = max(((match_score(k, o), k) for k in names), default=(0.0, ""), key=lambda x: x[0])
                best = f"name:{best}"
            out.append((s, key, best))
        return out

    def detect_groups(self, lines: list[str]) -> list[frozenset[str]]:
        """Candidate quest keys per objective in the list (narrowed by its title when readable)."""
        return list(dict.fromkeys(d.pref or d.full for d in self._detect(lines)))

    def _detect(self, lines: list[str]) -> list[Detection]:
        out: list[Detection] = []
        for e in parse_panel(lines):
            if any(is_hoard_title(t) for t in e.titles):
                # The Hoard event: not in the catalog; the app shows all its items instead.
                out.append(Detection(HOARD_KEY, frozenset(), None, HOARD_LABEL, HOARD_LABEL))
                e.titles = [t for t in e.titles if not is_hoard_title(t)]
            # Title lines: each one alone, or two lines joined when a long title wrapped.
            singles: list[tuple[set[str], str]] = []
            i = 0
            while i < len(e.titles):
                text = e.titles[i]
                hit = _best(self._score_titles(text), self.threshold)
                if not hit and i + 1 < len(e.titles):
                    text = e.titles[i] + " " + e.titles[i + 1]
                    hit = _best(self._score_titles(text), self.threshold)
                    if hit:
                        i += 1
                if hit:
                    singles.append((hit, self._title_name(text) or self._name_of[next(iter(hit))]))
                i += 1
            if not e.objectives:
                out.extend(Detection(f"title:{normalize(n)}", frozenset(h), None, n, n) for h, n in singles)
                continue
            own_hit, own_name = singles[-1] if singles else (set(), None)
            if own_name is None and e.titles:
                own_name = self._title_name(" ".join(e.titles))
            # Title-only quests stacked above this one:
            out.extend(Detection(f"title:{normalize(n)}", frozenset(h), None, n, n) for h, n in singles[:-1])
            matched_any = False
            # Every listed objective is active: the game takes finished ones off the list, so the
            # progress counter ("3/15") isn't consulted (OCR garbles it, e.g. "3/1 5").
            for obj in e.objectives:
                scored = self._score_objective(obj)
                top = max((s for s, _, _ in scored), default=0.0)
                if top < self.threshold:
                    continue
                matched_any = True
                tied = [(k, o) for s, k, o in scored if s >= top - 0.5]
                cands = frozenset(k for k, _ in tied)
                ids = sorted(objective_id(o) if not o.startswith("name:") else o for _, o in tied)
                key = ids[0]
                narrowed = cands & own_hit
                fallback = key.split(":", 1)[1].title() if ":" in key else key
                if key.startswith("name:"):
                    fallback = self._name_of[next(iter(cands))]
                out.append(Detection(key, cands, frozenset(narrowed) if narrowed else None, own_name, fallback))
            if not matched_any and own_hit:
                # Quest types whose objective wording we don't know: go by the title.
                out.append(Detection(f"title:{normalize(own_name or '')}", frozenset(own_hit), None,
                                     own_name, own_name or ""))
        return out

    def detect(self, lines: list[str]) -> set[str]:
        """All candidate quest keys that are active (flattened). Mostly for tests/tools."""
        out: set[str] = set()
        for g in self.detect_groups(lines):
            out |= g
        return out

    # ---------- state updates ----------
    def _merge(self, dets: list[Detection]) -> dict[str, Detection]:
        seen: dict[str, Detection] = {}
        for d in dets:
            if d.key in seen:
                o = seen[d.key]
                pref = (o.pref or frozenset()) | d.pref if d.pref else o.pref
                seen[d.key] = Detection(d.key, o.full | d.full, pref, o.label or d.label, o.fallback)
            else:
                seen[d.key] = d
        return seen

    @staticmethod
    def _same_title(a: str, b: str) -> bool:
        """OCR variants of one title, incl. fragments ("From The Dead" of "Scavenge From The Dead")."""
        na, nb = normalize(a), normalize(b)
        if fuzz.ratio(na, nb) >= 85:
            return True
        short, long_ = sorted((na, nb), key=len)
        return len(short) >= 8 and fuzz.partial_ratio(short, long_) >= 92

    def _apply(self, key: str, d: Detection, weight: float = 1.0) -> None:
        """Update (or create) an active item from a detection."""
        it = self._items.get(key)
        if it is None:
            it = self._items[key] = {"group": d.full, "pref": None, "labels": {}, "fallback": d.fallback,
                                     "misses": 0}
            self.added.append(d.label or d.fallback)
        it["misses"] = 0
        it["group"] = it["group"] | d.full  # every catalog quest with this objective
        if d.pref:
            it["pref"] = d.pref  # sticks until another title read says otherwise
        if d.label:
            # OCR variants of one title ("Scavenge from the Dead" / "Scavenge From Thedead") vote
            # together. A title the catalog says has this objective counts double; a catalog title
            # that doesn't have it (the objective got attached to the quest above because its own
            # title line was misread) counts half; titles missing from the catalog count normally.
            if any(self._name_of.get(k) == d.label for k in d.full):
                w = 2.0
            elif normalize(d.label) in self._names:
                w = 0.5
            else:
                w = 1.0
            same = next((l for l in it["labels"] if self._same_title(l, d.label)), None)
            label = same or d.label
            votes = it["labels"].pop(label, 0) + w * weight
            if same and len(d.label) > len(same):  # a fuller reading of the title: show that
                label = d.label
            it["labels"][label] = votes

    def update(self, lines: list[str]) -> list[frozenset[str]]:
        self.added, self.dropped = [], []
        if not lines:
            # Nothing readable (panel hidden, menu open...): keep what we had.
            return self.active
        seen = self._merge(self._detect(lines))
        if not seen and not looks_like_quest_list(lines):
            # Text, but nothing that looks like the quest list (a menu or the map covers it):
            # don't count that as every quest having disappeared.
            return self.active
        starting = not self._items  # nothing tracked yet (just started): no need to wait
        for key, d in seen.items():
            if key in self._items or starting:
                self._apply(key, d)
                self._pending.pop(key, None)
                continue
            p = self._pending.setdefault(key, {"hits": 0, "age": 0, "dets": []})
            p["hits"] += 1
            p["dets"].append(d)
            if p["hits"] >= self.CONFIRM_HITS:
                for det in p["dets"]:
                    self._apply(key, det)
                del self._pending[key]
        for key in list(self._pending):
            p = self._pending[key]
            p["age"] += 1
            if p["age"] >= self.CONFIRM_WINDOW:
                del self._pending[key]  # not seen often enough: a misread, not a quest
        # A read with fewer objective lines than we're tracking is clearly incomplete (a line was
        # garbled or cut off): it only counts half against the quests it missed. A quest that's
        # really gone still disappears after a few reads (or at once with a refresh).
        n_lines = sum(len(e.objectives) for e in parse_panel(lines))
        n_tracked = sum(1 for k in self._items if not k.startswith("title:"))
        # (No objective lines at all under the "Quests" header is an empty list, not a partial read.)
        step = 0.5 if 0 < n_lines < n_tracked else 1.0
        for key in list(self._items):
            if key not in seen:
                it = self._items[key]
                it["misses"] += step
                if it["misses"] > self.miss_tolerance:
                    self.dropped.append(self._display(it))
                    del self._items[key]
        return self.active

    def refresh(self, captures) -> list[frozenset[str]]:
        """Manual refresh (F9): rebuild the list from several captures taken moments apart, each
        read in several ways. A quest counts if it's in most captures (any reading of a capture
        will do), so one bad capture can't lose a quest and one garbled read can't add one.
        Labels and choices learned earlier are kept. If no capture shows a quest list at all
        (map or a menu open), nothing changes.

        `captures`: list of captures, each a list of reads (lists of lines). A plain list of reads
        is treated as one read per capture."""
        if captures and captures[0] and isinstance(captures[0][0], str):
            captures = [[r] for r in captures]
        valid = [c for c in captures if any(r and looks_like_quest_list(r) for r in c)]
        self.added, self.dropped = [], []
        if not valid:
            return self.active
        per_capture = [self._merge([d for r in c if r for d in self._detect(r)]) for c in valid]
        votes: dict[str, int] = {}
        for seen in per_capture:
            for key in seen:
                votes[key] = votes.get(key, 0) + 1
        need = 2 if len(valid) >= 2 else 1
        keep = {k for k, v in votes.items() if v >= need}
        old = self._items
        self._items, self._pending = {}, {}
        for key in keep:
            if key in old:
                self._items[key] = old[key]
            for seen in per_capture:
                if key in seen:
                    # F9 trusts what's on screen now: its title readings count 3x, but a title it
                    # couldn't read this time still falls back on what was read before.
                    self._apply(key, seen[key], weight=3.0)
        self.added = [self._display(self._items[k]) for k in keep if k not in old]
        self.dropped = [self._display(it) for k, it in old.items() if k not in keep]
        return self.active
