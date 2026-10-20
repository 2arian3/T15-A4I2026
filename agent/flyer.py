"""Mechanical handout checks and a bounded rewrite gate."""

import re

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions

NOT_APPROVED = "PLAN NOT APPROVED: needs a human decision. No flyer was written."
HEADINGS = [
    "## FLYER (ENGLISH)",
    "## FLYER (SECOND LANGUAGE)",
    "## TEXT MESSAGE",
    "## COMMUNITY HEALTH WORKER SCRIPT",
]
PLACEHOLDERS = {"[DATE]", "[TIME]", "[LOCATION]", "[CONTACT]", "[CONFIRM]"}
BANNED = re.compile(
    r"\b(race|ethnicity|minority|underserved|at[\s-]+risk|cure|diagnose)\b", re.I
)
STATISTIC = re.compile(
    r"\d|[%\uFF05]|\b(percent|percentage|statistics?|half|quarter|majority|minority|(?:one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million)\s+(?:in|of|people|residents|patients|percent))\b",
    re.I,
)
CLAIM = re.compile(
    r"\b(free|no[\s-]+insurance|without insurance|uninsured|insurance (?:is )?not (?:needed|required)|sliding[\s-]+scale|gratis|gratuit[oa]s?|sin seguro|sin aseguranza|escala m[oó]vil)\b",
    re.I,
)


def reading_grade(text: str) -> float:
    """Flesch-Kincaid with a deterministic vowel-group syllable estimate."""
    # shortcut: approximate syllables, use a validated dictionary for publication grading.
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
    if not words:
        return 0
    syllables = 0
    for word in words:
        word = word.lower()
        count = len(re.findall(r"[aeiouy]+", word))
        if word.endswith("e") and not word.endswith(("le", "ye")) and count > 1:
            count -= 1
        syllables += max(1, count)
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    return 0.39 * len(words) / sentences + 11.8 * syllables / len(words) - 15.59


def check_flyer(text: str) -> list[str]:
    if text.strip() == NOT_APPROVED:
        return []
    problems = []
    headings = re.findall(r"^## .+$", text, re.M)
    if headings != HEADINGS or text[: text.find(HEADINGS[0])].strip():
        return ["Use exactly the four fixed headings in order, without a preamble."]
    sections = []
    for i, heading in enumerate(HEADINGS):
        end = text.index(HEADINGS[i + 1]) if i + 1 < len(HEADINGS) else len(text)
        sections.append(text[text.index(heading) + len(heading) : end].strip())
    english, _second, sms, script = sections
    if any(not section for section in sections):
        problems.append("Every section must contain text.")
    if len(english.split()) >= 90:
        problems.append("English flyer must be under 90 words.")
    plain_english = re.sub(r"\[[^\]]*\]", "here", english)
    if reading_grade(plain_english) > 6:
        problems.append("English flyer must be at or below Flesch-Kincaid grade 6.")
    if len(sms) > 150:
        problems.append("Text message must be at most 150 characters.")
    lines = [line for line in script.splitlines() if line.strip()]
    if len(lines) != 6 or any(len(line.split()) > 20 for line in lines):
        problems.append(
            "Health worker script must have six short lines (at most 20 words each)."
        )
    used = set(re.findall(r"\[[^\]]*\]", text))
    if used - PLACEHOLDERS:
        problems.append("Use only [DATE] [TIME] [LOCATION] [CONTACT] [CONFIRM].")
    for name, section in zip(
        ("English", "second language", "text message", "script"), sections, strict=True
    ):
        if not PLACEHOLDERS.difference({"[CONFIRM]"}).issubset(
            set(re.findall(r"\[[^\]]*\]", section))
        ):
            problems.append(
                f"Keep all four logistical placeholders in the {name} section."
            )
    body = "\n".join(sections)
    if BANNED.search(body):
        problems.append("Remove banned words about groups, risk, diagnosis or cure.")
    if STATISTIC.search(body):
        problems.append(
            "Remove all numbers, percentages and statistics; use placeholders for logistics."
        )
    if re.search(
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:am|pm|o'clock)\b|\b\w+\s+(?:street|st\.|avenue|ave\.|road|rd\.|boulevard|lane)\b",
        body,
        re.I,
    ):
        problems.append("Use placeholders for addresses and times.")
    for sentence in re.split(r"[.!?;\n]+", body):
        if CLAIM.search(sentence) and "[CONFIRM]" not in sentence:
            problems.append(
                "Every free, insurance or sliding-scale claim needs [CONFIRM] in the same sentence."
            )
            break
    return problems


class FlyerChecks(BaseAgent):
    async def _run_async_impl(self, ctx):
        state = ctx.session.state
        problems = check_flyer(state.get("flyer_text", ""))
        if (
            state.get("plan_status") != "APPROVED"
            and state.get("flyer_text", "").strip() != NOT_APPROVED
        ):
            problems.append(
                "Only the PLAN NOT APPROVED sentence is allowed for an unapproved plan."
            )
        attempt = state.get("flyer_attempts", 0) + 1
        delta = {
            "flyer_check": "\n".join(problems),
            "flyer_status": "FAIL" if problems else "PASS",
            "flyer_attempts": attempt,
        }
        state.update(delta)
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            actions=EventActions(
                state_delta=delta, escalate=not problems or attempt >= 3
            ),
        )
