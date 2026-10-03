"""Gemma through a local Ollama server. Nothing here talks to the internet."""
from __future__ import annotations

import json
import os
import re
import urllib.request

def _base_url() -> str:
    """OLLAMA_HOST is often a bind address like 0.0.0.0 or 0.0.0.0:11434; talk to localhost then."""
    h = os.environ.get("OLLAMA_HOST", "").strip()
    if not h or h.split(":")[0] in ("0.0.0.0", "", "::"):
        return "http://localhost:11434"
    if not h.startswith("http"):
        h = "http://" + h
    return h if ":" in h.split("//", 1)[1] else h + ":11434"


OLLAMA = _base_url()
MODEL = os.environ.get("TILTCHECK_MODEL", "gemma4:e4b")

LANG = {
    "en": "Answer in plain English.",
    "ko": "한국어로 답하세요. 존댓말, 짧은 문장.",
}


def chat(system: str, user: str, *, temperature: float = 0.2, fmt: str | None = None) -> str:
    body = {"model": MODEL, "stream": False, "think": False,
            "options": {"temperature": temperature},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if fmt:
        body["format"] = fmt
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["message"]["content"].strip()


COACH = ("You are a calm trading coach reading one trader's own journal. Use only the numbers given. "
         "Do not predict markets, do not tell them what to buy or sell, do not promise results. "
         "Point at behaviour they control: when they trade, how they size, what they do after a loss.")


def coach_note(facts_text: str, lang: str = "en") -> str:
    return chat(COACH + " " + LANG[lang],
                "Here are the facts from my trade history:\n" + facts_text +
                "\n\nWrite 4 short bullet points: the two habits that cost me the most and the two that work. "
                "Quote the numbers exactly as given.")


def parse_plan(text: str) -> dict:
    """'short 2 MNQ right after a stop' -> {"instrument": "MNQ", "side": "short", "qty": 2}"""
    out = chat("Extract the planned futures trade from the user's note. Reply with JSON only: "
               '{"instrument": root symbol like MNQ, MES, MGC, MCL, "side": "long" or "short", "qty": integer}.',
               text, temperature=0.0, fmt="json")
    d = json.loads(out)
    d["instrument"] = re.sub(r"[^A-Z0-9]", "", str(d.get("instrument", "")).upper())
    d["side"] = "short" if str(d.get("side", "")).lower().startswith(("s", "매도", "숏")) else "long"
    d["qty"] = max(1, int(d.get("qty") or 1))
    return d


def explain_check(plan: str, prob: float, base_rate: float, similar: str, facts_text: str, lang: str = "en") -> str:
    return chat(COACH + " " + LANG[lang],
                f"I'm about to take this trade: {plan}\n"
                f"My overall win rate is {base_rate:.0%}.\n"
                f"Groups from my own history that this trade belongs to:\n{facts_text}\n\n"
                f"Most similar past trades:\n{similar}\n\n"
                "Write 3 short sentences. Sentence 1: the group above with the lowest win rate or the biggest loss, "
                "quoted with its exact numbers, compared with my overall win rate. Sentence 2: the group that looks "
                "best, with its numbers. Sentence 3: one question I could ask myself before clicking. "
                "Do not tell me to take or skip the trade, and do not mention the model percentage.")
