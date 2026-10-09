from __future__ import annotations

from dataclasses import dataclass
from typing import Any

UNVERSIONED = "none"


@dataclass(frozen=True, slots=True)
class Version:
    id: str
    candidate: bool = False

    @property
    def arm(self) -> str:
        return "candidate" if self.candidate else "stable"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Version:
        return cls(id=d["id"], candidate=d.get("candidate", False))


def version_label(version: Version | None) -> str:
    return UNVERSIONED if version is None else version.id
