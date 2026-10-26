r"""Deterministic dynamic prompts (#129): ``{a|b}`` choices and ``__name__`` wildcard lists, resolved offline.

Syntax (a backslash escapes the next character, so ``\{`` ``\}`` ``\|`` and ``\\`` are literal):

- ``{red|blue|green}`` picks one option; an option may be empty (``{|, detailed}`` is "maybe add ``, detailed``").
- ``{2$$a|b|c|d}`` picks two different options and joins them with ``, ``; ``{1-2$$...}`` picks one or two.
- ``{3::red|1::blue}`` weights an option for random sampling (default 1; ignored when every combination is listed).
- ``__colors__`` is a random entry of the wildcard list ``colors``. Entries and options may nest more of this syntax.

``variants`` returns resolved prompts either as a seeded random sample of distinct prompts or as every combination.
Pure: no I/O, no network, and error messages name positions and wildcards, never the prompt text.
"""

import random
import re
import zlib
from contextvars import ContextVar
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations, product

from dtflow.core.models import MAX_QUEUE_ITEMS
from dtflow.services.plan_errors import PlanError

MAX_DEPTH = 4  # nested braces and wildcard entries; also stops a wildcard that names itself
CAP = 10**9  # variant counts saturate here instead of growing without bound
_HEADER = re.compile(r"\s*(\d+)(?:-(\d+))?\$\$")
_WEIGHT = re.compile(r"\s*(\d+(?:\.\d+)?)::")
_WILDCARD = re.compile(r"__([A-Za-z0-9]+(?:[_\-][A-Za-z0-9]+)*)__")
PROMPT_TAGS_KEY = "prompt.variation_runs"  # request evidence: ``encode_runs`` of the runs variations produced
_marker: ContextVar[str] = ContextVar("dtflow_prompt_marker", default="\ue000")
# ``_resolve`` picks a private-use marker character absent from its inputs; ``_mark`` puts it before each character that
# came from a variation and ``_untag`` removes it, so a literal private-use character in a prompt is left alone.


@dataclass(frozen=True, slots=True)
class Text:
    value: str


@dataclass(frozen=True, slots=True)
class Wild:
    name: str


@dataclass(frozen=True, slots=True)
class Choice:
    options: tuple[tuple[float, tuple["Node", ...]], ...]  # (weight, content)
    low: int
    high: int


Node = Text | Wild | Choice


def _fail(message: str) -> PlanError:
    return PlanError(message)


class UndefinedWildcard(PlanError):
    """A template names a wildcard list nobody has supplied (yet): planning defers counting until lists are bound."""


def parse(template: str, depth: int = 0) -> tuple[Node, ...]:
    """The template as nodes; unbalanced braces, bad counts and too-deep nesting raise ``PlanError``."""
    nodes, end = _sequence(template, 0, "", depth)
    if end != len(template):  # only a stray closing brace stops a top-level sequence early
        raise _fail(f"The prompt has an unmatched '}}' at position {end + 1}.")
    return nodes


def _sequence(text: str, start: int, stops: str, depth: int) -> tuple[tuple[Node, ...], int]:
    nodes: list[Node] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            nodes.append(Text("".join(buffer)))
            buffer.clear()

    i = start
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            buffer.append(text[i + 1])
            i += 2
        elif char in stops or char == "}":
            break
        elif char == "{":
            flush()
            node, i = _choice(text, i, depth + 1)
            nodes.append(node)
        elif text.startswith("__", i) and (match := _WILDCARD.match(text, i)):
            flush()
            nodes.append(Wild(match.group(1)))
            i = match.end()
        else:
            buffer.append(char)
            i += 1
    flush()
    return tuple(nodes), i


def _choice(text: str, start: int, depth: int) -> tuple[Choice, int]:
    if depth > MAX_DEPTH:
        raise _fail(f"The prompt nests choices more than {MAX_DEPTH} levels deep (position {start + 1}).")
    i = start + 1
    low = high = 1
    if header := _HEADER.match(text, i):
        low = int(header.group(1))
        high = int(header.group(2) or low)
        i = header.end()
    options: list[tuple[float, tuple[Node, ...]]] = []
    while True:
        weight = 1.0
        if marker := _WEIGHT.match(text, i):
            weight = float(marker.group(1))
            i = marker.end()
            if weight <= 0:
                raise _fail(f"An option weight must be above zero (position {start + 1}).")
        content, i = _sequence(text, i, "|", depth)
        options.append((weight, content))
        if i >= len(text):
            raise _fail(f"The prompt has an unclosed '{{' at position {start + 1}.")
        if text[i] == "}":
            i += 1
            break
        i += 1  # the '|'
    if not 1 <= low <= high <= len(options):
        raise _fail(f"The choice at position {start + 1} picks {low}-{high} of only {len(options)} option(s).")
    return Choice(tuple(options), low, high), i


class Lists:
    """Wildcard lists by name, parsed once; ``used`` names the ones a template (and their entries) reached."""

    def __init__(self, entries: Mapping[str, tuple[str, ...]]):
        self._entries = dict(entries)
        self._parsed: dict[str, tuple[tuple[Node, ...], ...]] = {}
        self.counts: dict[str, int] = {}  # wildcard name -> its variant count, so nested lists are counted once
        self._nesting: dict[str, int] = {}  # wildcard name -> the longest wildcard chain starting at it
        self._visiting: set[str] = set()
        self.used: set[str] = set()

    def _load(self, name: str) -> tuple[tuple[Node, ...], ...]:
        if name not in self._entries:
            raise UndefinedWildcard(f"Wildcard '{name}' is not defined: add a list named {name} or remove __{name}__.")
        if name not in self._parsed:
            entries = [entry for entry in self._entries[name] if entry.strip()]
            if not entries:
                raise _fail(f"Wildcard '{name}' has no entries.")
            self._parsed[name] = tuple(parse(entry) for entry in entries)
        return self._parsed[name]

    def nesting(self, name: str) -> int:
        """How many wildcards deep a chain starting at ``name`` goes (1 for a list that names none); cycles raise."""
        if name not in self._nesting:
            if name in self._visiting:
                raise _fail(f"Wildcard '{name}' names itself (directly or through other lists).")
            self._visiting.add(name)
            try:
                self._nesting[name] = 1 + max(
                    (self.nesting(inner) for entry in self._load(name) for inner in _wild_names(entry)), default=0
                )
            finally:
                self._visiting.discard(name)
        return self._nesting[name]

    def get(self, name: str, depth: int) -> tuple[tuple[Node, ...], ...]:
        """The parsed entries; ``depth`` is how many wildcards deep the caller already is. Order-independent."""
        entries = self._load(name)
        if depth + self.nesting(name) - 1 > MAX_DEPTH:
            raise _fail(f"Wildcard '{name}' nests wildcards more than {MAX_DEPTH} levels deep.")
        self.used.add(name)
        return entries


def _wild_names(nodes: tuple[Node, ...]):
    for node in nodes:
        if isinstance(node, Wild):
            yield node.name
        elif isinstance(node, Choice):
            for _, content in node.options:
                yield from _wild_names(content)


def count(nodes: tuple[Node, ...], lists: Lists, depth: int = 0) -> int:
    """How many structural variants the nodes have (every combination), saturating at ``CAP``."""
    total = 1
    for node in nodes:
        total = min(CAP, total * _node_count(node, lists, depth))
    return total


def _node_count(node: Node, lists: Lists, depth: int) -> int:
    if isinstance(node, Text):
        return 1
    if isinstance(node, Wild):
        entries = lists.get(node.name, depth + 1)  # also tracks use and rejects too-deep nesting
        if node.name not in lists.counts:
            lists.counts[node.name] = min(CAP, sum(count(entry, lists, depth + 1) for entry in entries))
        return lists.counts[node.name]
    ways = [1] + [0] * node.high  # ways[j]: ways to have chosen j options so far
    for _, content in node.options:
        variants = count(content, lists, depth)
        for j in range(node.high, 0, -1):
            ways[j] = min(CAP, ways[j] + ways[j - 1] * variants)
    return min(CAP, sum(ways[node.low : node.high + 1]))


Runs = tuple[tuple[int, int], ...]


def _mark(text: str) -> str:
    """Tag every character that is not whitespace or a comma (the separators edits split on); idempotent."""
    out: list[str] = []
    tagged = False
    for char in text:
        if char == _marker.get():
            tagged = True
            out.append(char)
            continue
        out.append(char if tagged or char.isspace() or char == "," else _marker.get() + char)
        tagged = False
    return "".join(out)


def _untag(text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    """The prompt without markers, and ``(start, length)`` of each run that came from a variation.

    Runs separated by only whitespace join into one, so ``{red car|blue}`` is one tag, ``red car``.
    """
    clean: list[str] = []
    spans: list[list[int]] = []
    tagged = False
    gap_is_space = False
    for char in text:
        if char == _marker.get():
            tagged = True
            continue
        position = len(clean)
        clean.append(char)
        if tagged:
            if spans and gap_is_space:
                spans[-1][1] = position + 1 - spans[-1][0]
            else:
                spans.append([position, 1])
            gap_is_space = True
        elif not char.isspace():
            gap_is_space = False
        tagged = False
    return "".join(clean), tuple((start, length) for start, length in spans)


def _tidy(text: str) -> str:
    return re.sub(r"\s+,", ",", re.sub(r"[ \t]{2,}", " ", text)).strip()


def _pick_one(rng: random.Random, weights: list[float]) -> int:
    return rng.choices(range(len(weights)), weights=weights)[0]


def _sample(nodes: tuple[Node, ...], lists: Lists, rng: random.Random, depth: int) -> str:
    parts = []
    for node in nodes:
        if isinstance(node, Text):
            parts.append(node.value)
        elif isinstance(node, Wild):
            entries = lists.get(node.name, depth + 1)
            parts.append(_mark(_sample(entries[rng.randrange(len(entries))], lists, rng, depth + 1)))
        else:
            wanted = rng.randint(node.low, node.high)
            remaining = list(range(len(node.options)))
            chosen = []
            for _ in range(wanted):
                index = remaining.pop(_pick_one(rng, [node.options[i][0] for i in remaining]))
                chosen.append(index)
            parts.append(_mark(", ".join(_sample(node.options[i][1], lists, rng, depth) for i in sorted(chosen))))
    return "".join(parts)


def _all(nodes: tuple[Node, ...], lists: Lists, depth: int) -> list[str]:
    columns = [_node_all(node, lists, depth) for node in nodes]
    return ["".join(parts) for parts in product(*columns)]


def _node_all(node: Node, lists: Lists, depth: int) -> list[str]:
    if isinstance(node, Text):
        return [node.value]
    if isinstance(node, Wild):
        return [_mark(text) for entry in lists.get(node.name, depth + 1) for text in _all(entry, lists, depth + 1)]
    results = []
    for size in range(node.low, node.high + 1):
        for subset in combinations(node.options, size):
            columns = [_all(content, lists, depth) for _, content in subset]
            results.extend(_mark(", ".join(parts)) for parts in product(*columns))
    return results


@dataclass(frozen=True, slots=True)
class Mutation:
    """Seeded edits applied to each resolved prompt: drop up to ``drop`` parts, add up to ``add_max`` of ``add``, shuffle.

    A part is a comma-separated phrase (``shuffle="parts"``) or a single word (``"words"``). At least one part stays.
    """

    shuffle: str | None = None
    drop: int = 0
    add: tuple[str, ...] = ()
    add_max: int = 0

    @property
    def active(self) -> bool:
        return bool(self.shuffle or self.drop or (self.add and self.add_max))

    def describe(self) -> str:
        bits = [f"shuffle {self.shuffle}"] if self.shuffle else []
        bits += [f"drop up to {self.drop}"] if self.drop else []
        bits += [f"add up to {self.add_max} of {len(self.add)}"] if self.add and self.add_max else []
        return ", ".join(bits)


def check_mutation(mutation: Mutation) -> None:
    if mutation.shuffle not in (None, "parts", "words"):
        raise _fail("Shuffle reorders the comma-separated parts or the words.")
    if type(mutation.drop) is not int or not 0 <= mutation.drop <= 50:
        raise _fail("Dropping takes a whole number of parts in 0..50.")
    if type(mutation.add_max) is not int or not 0 <= mutation.add_max <= 50:
        raise _fail("Adding takes a whole number of parts in 0..50.")
    if mutation.add_max and not mutation.add:
        raise _fail("Adding parts needs a list of parts to add from.")
    if any(not isinstance(part, str) or not part.strip() for part in mutation.add):
        raise _fail("Parts to add are non-empty text.")


def _mutate(text: str, rng: random.Random, mutation: Mutation) -> str:
    words = mutation.shuffle == "words"
    parts = text.split() if words else [part.strip() for part in text.split(",") if part.strip()]
    if mutation.drop and len(parts) > 1:
        for _ in range(rng.randint(0, min(mutation.drop, len(parts) - 1))):
            parts.pop(rng.randrange(len(parts)))
    if mutation.add and mutation.add_max:
        for extra in rng.sample(mutation.add, rng.randint(0, min(mutation.add_max, len(mutation.add)))):
            parts.insert(rng.randint(0, len(parts)), extra.strip())
    if mutation.shuffle:
        rng.shuffle(parts)
    return (" " if words else ", ").join(parts)


def _non_empty(texts) -> tuple[str, ...]:
    """The distinct non-empty resolved prompts; a template that only resolves to nothing is an error."""
    kept = tuple(text for text in texts if text)
    if not kept:
        raise _fail("The prompt resolves to nothing; keep some text outside the choices.")
    return kept


def _resolve(
    template: str,
    wildcards: Mapping[str, tuple[str, ...]],
    *,
    strategy: str,
    n: int | None,
    rng_seed: int,
    limit: int,
    mutation: Mutation | None,
) -> tuple[tuple[tuple[str, Runs], ...], tuple[str, ...]]:
    """(each distinct prompt with its variation runs, wildcard names used)."""
    present = set(template).union(*(set(entry) for entries in wildcards.values() for entry in entries))
    present.update(char for part in (mutation.add if mutation is not None else ()) for char in part)
    marker = next(chr(code) for code in range(0xE000, 0xF900) if chr(code) not in present)
    token = _marker.set(marker)
    try:
        marked, used = _resolve_marked(
            template, wildcards, strategy=strategy, n=n, rng_seed=rng_seed, limit=limit, mutation=mutation
        )
        return tuple(_untag(text) for text in marked), used
    finally:
        _marker.reset(token)


def _resolve_marked(
    template: str,
    wildcards: Mapping[str, tuple[str, ...]],
    *,
    strategy: str,
    n: int | None,
    rng_seed: int,
    limit: int = MAX_QUEUE_ITEMS,
    mutation: Mutation | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(marked prompts, wildcard names used), distinct once markers are removed; see ``variants``."""
    lists = Lists(wildcards)
    nodes = parse(template)
    total = count(nodes, lists)
    mutation = mutation if mutation is not None and mutation.active else None
    if mutation is not None:
        check_mutation(mutation)
        rng = random.Random(f"dtflow-prompt/{rng_seed}")
        if strategy == "all":
            if total > limit:
                raise _fail(f"The prompt has {total} combinations; one queue holds at most {limit}.")
            unique = _distinct(_tidy(_mutate(_tidy(text), rng, mutation)) for text in _all(nodes, lists, 0))
            return _non_empty(unique), tuple(sorted(lists.used))
        if type(n) is not int or not 1 <= n <= limit:
            raise _fail(f"A random sample of prompts needs a count in 1..{limit}.")
        seen: dict[str, str] = {}
        attempts = 0
        while len(seen) < n and attempts < 200 * n + 1000:  # edits have no exhaustive fallback, so allow more tries
            text = _tidy(_mutate(_tidy(_sample(nodes, lists, rng, 0)), rng, mutation))
            if text:
                seen.setdefault(_untag(text)[0], text)
            attempts += 1
        if len(seen) < n:
            if not seen:
                raise _fail("The prompt resolves to nothing; keep some text outside the choices.")
            raise _fail(f"Only {len(seen)} different prompts could be made; ask for fewer or allow more edits.")
        return tuple(seen.values()), tuple(sorted(lists.used))
    if strategy == "all":
        if total > limit:
            raise _fail(f"The prompt has {total} combinations; one queue holds at most {limit}.")
        unique = _non_empty(_distinct(_tidy(text) for text in _all(nodes, lists, 0)))
        return unique, tuple(sorted(lists.used))
    if type(n) is not int or not 1 <= n <= limit:
        raise _fail(f"A random sample of prompts needs a count in 1..{limit}.")
    if total < n:
        raise _fail(f"The prompt has only {total} different variants; {n} were asked for.")
    rng = random.Random(f"dtflow-prompt/{rng_seed}")
    seen: dict[str, str] = {}
    attempts = 0
    while len(seen) < n and attempts < 20 * n + 200:
        text = _tidy(_sample(nodes, lists, rng, 0))
        if text:
            seen.setdefault(_untag(text)[0], text)
        attempts += 1
    if len(seen) < n:  # a nearly exhausted space: finish uniformly from the unseen combinations
        if total > limit:
            raise _fail(f"Could not draw {n} different prompts; ask for fewer or add more choices.")
        rest = [
            text for text in _distinct(_tidy(t) for t in _all(nodes, lists, 0)) if text and _untag(text)[0] not in seen
        ]
        for text in rng.sample(rest, min(len(rest), n - len(seen))):
            seen[_untag(text)[0]] = text
        if len(seen) < n:
            if not seen:
                raise _fail("The prompt resolves to nothing; keep some text outside the choices.")
            raise _fail(f"The prompt has only {len(seen)} different resolved prompts; {n} were asked for.")
    return tuple(seen.values()), tuple(sorted(lists.used))


def _distinct(texts) -> tuple[str, ...]:
    """The marked texts whose unmarked prompt is new, in order."""
    unique: dict[str, str] = {}
    for text in texts:
        unique.setdefault(_untag(text)[0], text)
    return tuple(unique.values())


def variants(
    template: str,
    wildcards: Mapping[str, tuple[str, ...]],
    *,
    strategy: str,
    n: int | None,
    rng_seed: int,
    limit: int = MAX_QUEUE_ITEMS,
    mutation: Mutation | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(resolved prompts, wildcard names used). ``random`` draws ``n`` distinct prompts, ``all`` lists every one."""
    resolved, used = _resolve(
        template, wildcards, strategy=strategy, n=n, rng_seed=rng_seed, limit=limit, mutation=mutation
    )
    return tuple(text for text, _ in resolved), used


def tagged_variants(
    template: str,
    wildcards: Mapping[str, tuple[str, ...]],
    *,
    strategy: str,
    n: int | None,
    rng_seed: int,
    limit: int = MAX_QUEUE_ITEMS,
    mutation: Mutation | None = None,
) -> tuple[tuple[tuple[str, tuple[tuple[int, int], ...]], ...], tuple[str, ...]]:
    """Like ``variants``, but each prompt comes with ``(start, length)`` of the runs its variations produced.

    Text that is only whitespace or commas is never part of a run, so a bare ``, `` joining two picks stays plain.
    """
    return _resolve(template, wildcards, strategy=strategy, n=n, rng_seed=rng_seed, limit=limit, mutation=mutation)


def encode_runs(prompt: str, runs: Runs) -> str:
    """``checksum|start:length,...``: the checksum ties the runs to this exact prompt text."""
    return f"{zlib.crc32(prompt.encode()):08x}|" + ",".join(f"{start}:{length}" for start, length in runs)


def decode_runs(prompt: str, value: str | None) -> Runs:
    """The runs ``encode_runs`` stored for ``prompt``; empty when the prompt was edited since or the value is damaged."""
    try:
        checksum, _, body = (value or "").partition("|")
        if checksum != f"{zlib.crc32(prompt.encode()):08x}":
            return ()
        runs = tuple((int(a), int(b)) for a, b in (part.split(":") for part in body.split(",") if part))
    except ValueError:
        return ()
    end = 0
    for start, length in runs:  # sorted, non-empty and non-overlapping, all inside the prompt
        if start < end or length <= 0 or start + length > len(prompt):
            return ()
        end = start + length
    return runs
