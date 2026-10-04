"""Versioned prompt templates, stored as `prompts/<name>.v<version>.md` inside the package.

A run records each prompt's id and content hash, so results can be tied to the exact text.
Change a prompt by adding a new version file, not by editing an existing one.
"""

import hashlib
from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def id(self) -> str:
        return f"{self.name}.v{self.version}"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


@cache
def load_prompt(name: str, version: int) -> Prompt:
    resource = resources.files("pr_investigator") / "prompts" / f"{name}.v{version}.md"
    return Prompt(name=name, version=version, text=resource.read_text(encoding="utf-8").strip())
