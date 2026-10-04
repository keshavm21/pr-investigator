"""Evaluation case manifests (YAML) and their loader.

A case is a pull request against a shared fixture project: a list of edits applied to the
fixture, plus the issues a reviewer should report (`expected`) and the places it must not flag
(`must_not_flag`, used by decoys). Locations are given as a text `match` inside the head
version of a file, so they don't break when the fixture changes elsewhere.
"""

from enum import StrEnum
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from pr_investigator.domain import Category, Severity
from pr_investigator.errors import EvalCaseError


class CaseKind(StrEnum):
    SEEDED = "seeded"  # contains at least one known issue
    DECOY = "decoy"  # looks suspicious but is safe for a reason outside the diff
    CLEAN = "clean"  # no known issue


class Edit(BaseModel):
    path: str
    find: str | None = None
    replace: str | None = None
    create: str | None = None
    delete: bool = False

    @model_validator(mode="after")
    def _one_operation(self) -> Self:
        modes = [self.find is not None, self.create is not None, self.delete]
        if sum(modes) != 1:
            raise ValueError(f"edit for {self.path} needs exactly one of find, create, delete")
        if self.find is not None and self.replace is None:
            raise ValueError(f"edit for {self.path}: 'find' needs a 'replace'")
        return self


class LocationSpec(BaseModel):
    path: str
    match: str = Field(min_length=1, description="Text that occurs exactly once in the file")
    span: int = Field(default=1, ge=1, description="Number of lines, starting at the match")


class ExpectedIssue(BaseModel):
    id: str
    category: Category
    severity: Severity
    location: LocationSpec
    description: str


class MustNotFlag(BaseModel):
    location: LocationSpec
    reason: str


class EvalCase(BaseModel):
    id: str
    kind: CaseKind
    title: str
    description: str = ""
    fixture: str = "inventory_service"
    edits: list[Edit] = Field(min_length=1)
    expected: list[ExpectedIssue] = Field(default_factory=list)
    must_not_flag: list[MustNotFlag] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent_kind(self) -> Self:
        if self.kind is CaseKind.SEEDED and not self.expected:
            raise ValueError("seeded cases need at least one expected issue")
        if self.kind is CaseKind.DECOY and (self.expected or not self.must_not_flag):
            raise ValueError("decoy cases need must_not_flag regions and no expected issues")
        if self.kind is CaseKind.CLEAN and (self.expected or self.must_not_flag):
            raise ValueError("clean cases have no expected issues or must_not_flag regions")
        return self


def load_case(path: Path) -> EvalCase:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        case = EvalCase.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise EvalCaseError(f"{path.name}: {exc}") from exc
    if case.id != path.stem:
        raise EvalCaseError(f"{path.name}: id {case.id!r} must match the file name")
    return case


def load_cases(cases_dir: Path, only: list[str] | None = None) -> list[EvalCase]:
    if not cases_dir.is_dir():
        raise EvalCaseError(f"no cases directory at {cases_dir}")
    cases = [load_case(p) for p in sorted(cases_dir.glob("*.yaml"))]
    if only:
        known = {c.id for c in cases}
        unknown = sorted(set(only) - known)
        if unknown:
            raise EvalCaseError(f"unknown case ids: {', '.join(unknown)}")
        cases = [c for c in cases if c.id in only]
    return cases
