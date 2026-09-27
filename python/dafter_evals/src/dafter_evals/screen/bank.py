from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

PACKAGE = "dafter_evals.screen"
LANGUAGES = ("hi", "kn", "en", "mr", "te")


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class Bank:
    language: str
    name: str
    script: str | None
    register: str | None
    questions: tuple[Question, ...]

    @property
    def empty(self) -> bool:
        return not self.questions

    def refusal(self) -> str:
        return (
            f"the {self.name} ({self.language}) question bank is empty: add human-written "
            f"questions to dafter_evals/screen/banks/{self.language}.json, since the screen "
            "never generates its own"
        )


def parse(text: str) -> Bank:
    raw = json.loads(text)
    questions = tuple(Question(id=q["id"], text=q["text"]) for q in raw["questions"])
    if len({q.id for q in questions}) != len(questions):
        raise ValueError(f"the {raw['language']} bank repeats a question id")
    if any(not q.text.strip() for q in questions):
        raise ValueError(f"the {raw['language']} bank has a blank question")
    return Bank(
        language=raw["language"],
        name=raw["name"],
        script=raw["script"],
        register=raw["register"],
        questions=questions,
    )


def load(language: str) -> Bank:
    if language not in LANGUAGES:
        raise ValueError(f"no question bank for {language}: one of {', '.join(LANGUAGES)}")
    path = resources.files(PACKAGE).joinpath("banks", f"{language}.json")
    bank = parse(path.read_text(encoding="utf-8"))
    if bank.language != language:
        raise ValueError(f"banks/{language}.json declares language {bank.language}")
    return bank
