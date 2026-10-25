"""DAG registry: steps with declared input and output files.

A step declares the files it reads and the files it writes. Edges are *derived*
from those declarations -- step B depends on step A when one of B's inputs is
one of A's outputs -- so the graph can never drift out of sync with a
hand-maintained edge list.

Input patterns may be globs ("data/*.csv"), which is what lets "a new raw file
appeared" be a change the pipeline can see. Outputs must be concrete paths,
because exactly one step is allowed to produce a given file.
"""

from __future__ import annotations

import fnmatch
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

GLOB_CHARS = set("*?[")


def norm(path: str) -> str:
    """Normalize a workspace-relative path to posix form (used for state keys)."""
    p = Path(str(path)).as_posix()
    while p.startswith("./"):
        p = p[2:]
    return p


def is_glob(pattern: str) -> bool:
    return any(c in pattern for c in GLOB_CHARS)


def path_matches(pattern: str, path: str) -> bool:
    """Does a declared input pattern cover this path?"""
    pattern, path = norm(pattern), norm(path)
    return path == pattern or fnmatch.fnmatch(path, pattern)


@dataclass
class Step:
    """One unit of work: a command plus the files it reads and writes."""

    name: str
    command: list[str]                                # argv; "{python}" -> this interpreter
    inputs: list[str] = field(default_factory=list)   # workspace-relative paths or globs
    outputs: list[str] = field(default_factory=list)  # workspace-relative paths, no globs
    title: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        self.inputs = [norm(p) for p in self.inputs]
        self.outputs = [norm(p) for p in self.outputs]
        if not self.title:
            self.title = self.name.replace("_", " ")

    def argv(self) -> list[str]:
        return [sys.executable if a == "{python}" else a for a in self.command]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(
            name=d["name"],
            command=list(d["command"]),
            inputs=list(d.get("inputs", [])),
            outputs=list(d.get("outputs", [])),
            title=d.get("title", ""),
            description=d.get("description", ""),
        )


class DagError(ValueError):
    """The registered steps do not form a usable DAG."""


@dataclass
class Dag:
    steps: list[Step] = field(default_factory=list)

    # -- lookup ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self):
        return iter(self.steps)

    def __contains__(self, name: object) -> bool:
        return any(s.name == name for s in self.steps)

    def __getitem__(self, name: str) -> Step:
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(name)

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.steps]

    def producer_of(self, path: str) -> Optional[str]:
        """Which step writes this file, if any."""
        for s in self.steps:
            if any(norm(path) == o for o in s.outputs):
                return s.name
        return None

    # -- structure ---------------------------------------------------------
    def parents(self, name: str) -> list[str]:
        """Steps whose outputs feed this step's inputs."""
        step = self[name]
        out = []
        for other in self.steps:
            if other.name == name:
                continue
            if any(path_matches(pat, o) for pat in step.inputs for o in other.outputs):
                out.append(other.name)
        return out

    def children(self, name: str) -> list[str]:
        return [s.name for s in self.steps if name in self.parents(s.name)]

    def edges(self) -> list[tuple[str, str]]:
        """(upstream, downstream) pairs."""
        return [(p, s.name) for s in self.steps for p in self.parents(s.name)]

    def topo_order(self) -> list[str]:
        """Dependency order. Ties are broken by declaration order so runs are stable."""
        order_index = {s.name: i for i, s in enumerate(self.steps)}
        pending = {s.name: set(self.parents(s.name)) for s in self.steps}
        done: list[str] = []
        while pending:
            ready = sorted((n for n, deps in pending.items() if not deps), key=order_index.get)
            if not ready:
                raise DagError(f"cycle among steps: {', '.join(sorted(pending))}")
            for n in ready:
                done.append(n)
                pending.pop(n)
            for deps in pending.values():
                deps.difference_update(ready)
        return done

    def downstream(self, names: Iterable[str], include_self: bool = True) -> list[str]:
        """Every step reachable from `names`, in dependency order."""
        seen = set(names)
        frontier = list(seen)
        while frontier:
            for child in self.children(frontier.pop()):
                if child not in seen:
                    seen.add(child)
                    frontier.append(child)
        if not include_self:
            seen -= set(names)
        return [n for n in self.topo_order() if n in seen]

    def validate(self) -> None:
        seen: set[str] = set()
        for s in self.steps:
            if not s.name:
                raise DagError("step name must not be empty")
            if s.name in seen:
                raise DagError(f"duplicate step name: {s.name}")
            seen.add(s.name)
            if not s.command:
                raise DagError(f"step '{s.name}' has no command")
            if not s.outputs:
                raise DagError(f"step '{s.name}' declares no outputs; nothing to cache")
            for o in s.outputs:
                if is_glob(o):
                    raise DagError(f"step '{s.name}' output '{o}' is a glob; outputs must be exact paths")

        writers: dict[str, str] = {}
        for s in self.steps:
            for o in s.outputs:
                if o in writers:
                    raise DagError(f"'{o}' is written by both '{writers[o]}' and '{s.name}'")
                writers[o] = s.name
            for pat in s.inputs:
                if pat in s.outputs:
                    raise DagError(f"step '{s.name}' lists '{pat}' as both input and output")

        self.topo_order()  # raises on a cycle

    # -- serialization -----------------------------------------------------
    def to_dict(self) -> dict:
        return {"steps": [s.to_dict() for s in self.steps]}

    @classmethod
    def from_dict(cls, d: dict) -> "Dag":
        return cls(steps=[Step.from_dict(s) for s in d.get("steps", [])])

    def describe(self) -> dict:
        """Everything the frontend needs to draw the graph."""
        return {
            "steps": [
                {**s.to_dict(), "depends_on": self.parents(s.name), "feeds": self.children(s.name)}
                for s in self.steps
            ],
            "edges": [{"from": a, "to": b} for a, b in self.edges()],
            "order": self.topo_order(),
        }
