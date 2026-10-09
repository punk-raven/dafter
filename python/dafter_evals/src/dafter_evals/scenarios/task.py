from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dafter_core.enums import Stage
from dafter_providers.fallback import FAILOVER_STAGES
from dafter_runtime.personas import base_language

SCENARIOS_ROOT = Path(__file__).resolve().parents[5] / "testdata" / "scenarios"
POLICY_FILE = "policy.txt"
LANGUAGES = ("en-IN", "hi", "te-IN", "kn-IN", "mr-IN")
SETS = ("flows", "honesty", "consent", "faults")
TOOL_NAMES = frozenset(
    {
        "lookup_order",
        "create_ticket",
        "schedule_callback",
        "current_time",
        "who_is_here",
        "get_weather",
        "end_call",
    }
)
SWITCH_TOOL = "switch_language"
SIMULATED = "simulated"
SCRIPTED = "scripted"
TO_YOU = "you"
TO_THE_ROOM = "room"
CALLER = "caller"
DEFAULT_MAX_TURNS = 8


@dataclass(frozen=True, slots=True)
class Order:
    status: str
    item: str
    delivery: str


@dataclass(frozen=True, slots=True)
class Person:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class World:
    clock: str
    orders: dict[str, Order] = field(default_factory=dict)
    people: tuple[Person, ...] = ()


@dataclass(frozen=True, slots=True)
class ScriptedTurn:
    speaker: str
    text: str
    to: str
    reply: bool


@dataclass(frozen=True, slots=True)
class User:
    mode: str
    opening: str
    persona: str = ""
    goal: str = ""
    facts: dict[str, str] = field(default_factory=dict)
    turns: tuple[ScriptedTurn, ...] = ()


@dataclass(frozen=True, slots=True)
class ExpectedCall:
    tool: str
    arguments: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReadBack:
    kind: str
    forms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Expect:
    calls: tuple[ExpectedCall, ...] = ()
    effects: tuple[ExpectedCall, ...] = ()
    language: str | None = None
    ended: bool | None = None
    never_say: tuple[str, ...] = ()
    read_back: tuple[ReadBack, ...] = ()
    judge: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    language: str
    reviewed: bool
    scenario_set: str
    tags: tuple[str, ...]
    persona: str
    agent_name: str
    tools: tuple[str, ...]
    switchable: tuple[str, ...]
    faults: frozenset[Stage]
    world: World
    user: User
    expect: Expect
    max_turns: int = DEFAULT_MAX_TURNS

    @property
    def group(self) -> bool:
        return bool(self.world.people)


@dataclass(frozen=True, slots=True)
class Suite:
    language: str
    name: str
    set: str
    tasks: tuple[Task, ...]


def _strings(raw: Any, where: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(v, str) and v.strip() for v in raw):
        raise ValueError(f"{where}: a list of non-blank strings")
    return tuple(raw)


def _call(raw: dict[str, Any], where: str) -> ExpectedCall:
    tool = raw["tool"]
    if tool not in TOOL_NAMES and tool != SWITCH_TOOL:
        raise ValueError(f"{where}: {tool} is no scenario tool")
    arguments = raw.get("arguments") or {}
    return ExpectedCall(tool=tool, arguments={str(k): str(v) for k, v in arguments.items()})


def _expect(raw: dict[str, Any], where: str) -> Expect:
    read_back = tuple(
        ReadBack(kind=r["kind"], forms=_strings(r["forms"], f"{where} readBack"))
        for r in raw.get("readBack", ())
    )
    return Expect(
        calls=tuple(_call(c, f"{where} calls") for c in raw.get("calls", ())),
        effects=tuple(_call(c, f"{where} effects") for c in raw.get("effects", ())),
        language=raw.get("language"),
        ended=raw.get("ended"),
        never_say=_strings(raw.get("neverSay", []), f"{where} neverSay"),
        read_back=read_back,
        judge=_strings(raw.get("judge", []), f"{where} judge"),
    )


def _user(raw: dict[str, Any], where: str, people: tuple[Person, ...]) -> User:
    mode = raw["mode"]
    if mode == SIMULATED:
        if people:
            raise ValueError(f"{where}: a group call is scripted, never simulated")
        user = User(
            mode=mode,
            opening=raw["opening"],
            persona=raw["persona"],
            goal=raw["goal"],
            facts={str(k): str(v) for k, v in (raw.get("facts") or {}).items()},
        )
        if not (user.opening.strip() and user.persona.strip() and user.goal.strip()):
            raise ValueError(f"{where}: a simulated user needs an opening, a persona and a goal")
        return user
    if mode != SCRIPTED:
        raise ValueError(f"{where}: user mode is {SIMULATED} or {SCRIPTED}")
    known = {p.id for p in people} or {CALLER}
    turns = tuple(
        ScriptedTurn(speaker=t["speaker"], text=t["text"], to=t["to"], reply=bool(t["reply"]))
        for t in raw["turns"]
    )
    if not turns:
        raise ValueError(f"{where}: a scripted user needs at least one turn")
    for turn in turns:
        if turn.speaker not in known:
            raise ValueError(f"{where}: {turn.speaker} is not in the call")
        if turn.to not in (TO_YOU, TO_THE_ROOM):
            raise ValueError(f"{where}: a turn goes to {TO_YOU} or {TO_THE_ROOM}")
    return User(mode=mode, opening=turns[0].text, turns=turns)


def _world(raw: dict[str, Any]) -> World:
    orders = {
        str(k): Order(status=v["status"], item=v["item"], delivery=v["delivery"])
        for k, v in (raw.get("orders") or {}).items()
    }
    people = tuple(Person(id=p["id"], name=p["name"]) for p in raw.get("people", ()))
    return World(clock=raw["clock"], orders=orders, people=people)


def _faults(raw: Any, where: str) -> frozenset[Stage]:
    known = {str(s) for s in FAILOVER_STAGES}
    named = set(_strings(raw, f"{where} faults")) if raw else set()
    if named - known:
        raise ValueError(f"{where}: faults are among {', '.join(sorted(known))}")
    return frozenset(Stage(n) for n in named)


def _task(raw: dict[str, Any], language: str, scenario_set: str) -> Task:
    where = f"task {raw.get('id', '?')}"
    tools = _strings(raw["tools"], f"{where} tools")
    unknown = sorted(set(tools) - TOOL_NAMES)
    if unknown:
        raise ValueError(f"{where}: no scenario tool named {', '.join(unknown)}")
    switchable = _strings(raw.get("switchable", []), f"{where} switchable")
    if switchable and language not in switchable:
        raise ValueError(f"{where}: switchable languages include the task's own")
    if len({base_language(t) for t in switchable}) != len(switchable):
        raise ValueError(f"{where}: two switchable languages share a base language")
    world = _world(raw["world"])
    task = Task(
        id=raw["id"],
        language=language,
        reviewed=raw["reviewed"],
        scenario_set=scenario_set,
        tags=_strings(raw.get("tags", []), f"{where} tags"),
        persona=raw["persona"],
        agent_name=raw["agentName"],
        tools=tools,
        switchable=switchable,
        faults=_faults(raw.get("faults"), where),
        world=world,
        user=_user(raw["user"], where, world.people),
        expect=_expect(raw["expect"], where),
        max_turns=int(raw.get("maxTurns", DEFAULT_MAX_TURNS)),
    )
    if not isinstance(task.reviewed, bool):
        raise ValueError(f"{where}: reviewed is true or false")
    if task.max_turns < 1:
        raise ValueError(f"{where}: maxTurns is at least 1")
    offered = set(tools) | ({SWITCH_TOOL} if switchable else set())
    for call in (*task.expect.calls, *task.expect.effects):
        if call.tool not in offered:
            raise ValueError(f"{where}: expects {call.tool}, which the task does not offer")
    if task.expect.language and task.expect.language not in switchable:
        raise ValueError(f"{where}: expects a language the task cannot switch into")
    return task


def parse(text: str) -> Suite:
    raw = json.loads(text)
    language, scenario_set = raw["language"], raw["set"]
    if language not in LANGUAGES:
        raise ValueError(f"no scenario language {language}: one of {', '.join(LANGUAGES)}")
    if scenario_set not in SETS:
        raise ValueError(f"no scenario set {scenario_set}: one of {', '.join(SETS)}")
    tasks = tuple(_task(t, language, scenario_set) for t in raw["tasks"])
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError(f"the {scenario_set}/{language} scenarios repeat a task id")
    return Suite(language=language, name=raw["name"], set=scenario_set, tasks=tasks)


def load_set(scenario_set: str, language: str, root: Path = SCENARIOS_ROOT) -> Suite:
    if language not in LANGUAGES:
        raise ValueError(f"no scenarios for {language}: one of {', '.join(LANGUAGES)}")
    if scenario_set not in SETS:
        raise ValueError(f"no scenario set {scenario_set}: one of {', '.join(SETS)}")
    path = root / scenario_set / f"{language}.json"
    suite = parse(path.read_text(encoding="utf-8"))
    if (suite.set, suite.language) != (scenario_set, language):
        raise ValueError(f"{scenario_set}/{language}.json declares {suite.set}/{suite.language}")
    return suite


def load(
    language: str, root: Path = SCENARIOS_ROOT, sets: tuple[str, ...] = SETS
) -> tuple[Task, ...]:
    tasks = tuple(t for s in sets for t in load_set(s, language, root).tasks)
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError(f"the {language} scenario sets repeat a task id")
    return tasks


def policy(root: Path = SCENARIOS_ROOT) -> str:
    return (root / POLICY_FILE).read_text(encoding="utf-8").strip()
