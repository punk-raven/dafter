from typing import Any

from dafter_runtime.personas import SUPPORT_REF, persona_for

AGENT_NAME = "Nivya"


def nivya_turn(context: dict[str, Any]) -> list[dict[str, str]]:
    probe_vars = context["vars"]
    persona_ref = probe_vars.get("persona_ref") or SUPPORT_REF
    persona = persona_for(persona_ref, probe_vars["language"], AGENT_NAME)
    return [
        {"role": "system", "content": persona.instructions},
        {"role": "user", "content": probe_vars["probe"]},
    ]
