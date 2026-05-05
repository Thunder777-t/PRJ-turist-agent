from __future__ import annotations

import re


_EN_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
}


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def detect_user_language(text: str, default: str = "en") -> str:
    raw = str(text or "").strip()
    if not raw:
        return default
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", raw))
    latin_count = len(re.findall(r"[A-Za-z]", raw))
    if cjk_count > 0 and latin_count > 0:
        return "en" if latin_count >= cjk_count * 2 else "zh"
    if cjk_count > 0:
        return "zh"
    if latin_count > 0:
        return "en"
    return default


def extract_days_hint(text: str) -> int:
    raw = str(text or "")

    zh_digit = re.search(r"(\d{1,2})\s*(天|日)", raw)
    if zh_digit:
        try:
            return max(0, int(zh_digit.group(1)))
        except Exception:
            pass

    en_digit = re.search(r"(\d{1,2})\s*[- ]?\s*(day|days|night|nights)\b", raw, flags=re.IGNORECASE)
    if en_digit:
        try:
            return max(0, int(en_digit.group(1)))
        except Exception:
            pass

    en_word = re.search(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen)\s+"
        r"(day|days|night|nights)\b",
        raw,
        flags=re.IGNORECASE,
    )
    if en_word:
        return _EN_NUM_WORDS.get(en_word.group(1).lower(), 0)

    if re.search(r"(一周|一星期|一个星期|1周|1星期|one week)\b", raw, flags=re.IGNORECASE):
        return 7

    return 0


def extract_origin_city(text: str) -> str:
    raw = str(text or "")

    zh = re.search(r"从\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s]{1,20}?)\s*出发", raw)
    if zh:
        return str(zh.group(1) or "").strip()

    en = re.search(
        r"\bfrom\s+([A-Za-z][A-Za-z\s]{1,30}?)(?:\s+to|\s*,|\s+for|\s+in|\s+on|\s*$)",
        raw,
        flags=re.IGNORECASE,
    )
    if en:
        return str(en.group(1) or "").strip()

    return ""


def extract_budget_hint(text: str) -> str:
    raw = str(text or "")
    patterns = (
        r"预算\s*([0-9]+(?:\.[0-9]+)?\s*[wW万亿元]*)",
        r"\bbudget\s*(?:is|around|about|:)?\s*([0-9][0-9,]*(?:\.[0-9]+)?\s*(?:k|w|m|usd|rmb|cny)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            value = str(match.group(1) or "").strip()
            if value:
                return value
    return ""


def extract_destination_city(text: str) -> str:
    raw = str(text or "")

    zh_patterns = [
        r"去\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s]{1,20}?)(?:旅游|旅行|玩|行程|度假|[\s,，。！？]|$)",
        r"到\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s]{1,20}?)(?:旅游|旅行|玩|行程|度假|[\s,，。！？]|$)",
        r"([\u4e00-\u9fffA-Za-z]{2,16})\s*\d{1,2}\s*天\s*(?:行程|旅游|旅行|自由行|攻略)",
    ]
    for pattern in zh_patterns:
        match = re.search(pattern, raw)
        if match:
            value = _normalize_zh_destination_candidate(str(match.group(1) or "").strip())
            if value:
                return value

    en_patterns = [
        r"\bto\s+(?!travel(?:ing)?\b)([A-Za-z][A-Za-z\s]{1,40}?)(?:\s+for|\s+with|\s+from|\s+in|\s+on|\s+at|[,.;!?]|$)",
        r"\bgo\s+to\s+([A-Za-z][A-Za-z\s]{1,40}?)(?:\s+for|\s+with|\s+from|\s+in|\s+on|\s+at|[,.;!?]|$)",
        r"\btrip\s+to\s+([A-Za-z][A-Za-z\s]{1,40}?)(?:\s+for|\s+with|\s+from|\s+in|\s+on|\s+at|[,.;!?]|$)",
        r"\btravel(?:ing)?\s+to\s+([A-Za-z][A-Za-z\s]{1,40}?)(?:\s+for|\s+with|\s+from|\s+in|\s+on|\s+at|[,.;!?]|$)",
        r"\bplanning\s+(?:a|an|the)?\s*\d{1,2}\s*[- ]?\s*day\s+trip\s+to\s+([A-Za-z][A-Za-z\s]{1,40}?)(?:\s+for|\s+with|\s+from|\s+in|\s+on|\s+at|[,.;!?]|$)",
        r"\b(?:a|an|my|our|the)?\s*(?:\d{1,2}\s*[- ]?\s*day\s+)?([A-Za-z]{2,30}(?:\s+[A-Za-z]{2,30}){0,2})\s+trip\b",
        r"\b([A-Za-z]{2,30}(?:\s+[A-Za-z]{2,30}){0,2})\s+\d{1,2}\s*[- ]?\s*day\s+trip\b",
    ]
    for pattern in en_patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            value = _normalize_en_destination_candidate(str(match.group(1) or "").strip())
            if _is_plausible_en_destination(value):
                return value

    return ""


def _is_plausible_en_destination(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    words = [token.strip().lower() for token in text.split() if token.strip()]
    if not words or len(words) > 4:
        return False
    banned = {
        "day",
        "days",
        "night",
        "nights",
        "trip",
        "travel",
        "travelling",
        "traveling",
        "planning",
        "arrange",
        "arrangement",
        "following",
        "next",
        "tomorrow",
        "tonight",
        "depart",
        "departure",
        "yuan",
        "rmb",
        "budget",
        "how",
        "should",
        "plan",
        "from",
        "to",
        "itinerary",
        "direct",
        "want",
        "couple",
        "family",
        "solo",
        "friends",
        "friend",
        "parent",
        "parents",
    }
    if any(token in banned for token in words):
        return False
    return True


def _normalize_en_destination_candidate(value: str) -> str:
    tokens = [token for token in str(value or "").strip().split() if token]
    if not tokens:
        return ""
    prefixes = {
        "couple",
        "family",
        "solo",
        "friends",
        "friend",
        "parents",
        "parent",
        "a",
        "an",
        "the",
        "my",
        "our",
        "planning",
        "plan",
    }
    while tokens and tokens[0].lower() in prefixes:
        tokens = tokens[1:]
    return " ".join(tokens).strip()


def _normalize_zh_destination_candidate(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    prefixes = ("情侣", "亲子", "家庭", "朋友", "一个人", "我想", "想去")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix) :].strip()
                changed = True
    suffixes = ("行程", "旅游", "旅行", "自由行", "攻略")
    for suffix in suffixes:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)].strip()
    return text
