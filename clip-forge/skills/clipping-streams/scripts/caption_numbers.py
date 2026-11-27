"""Convert spoken number phrases to digits for display, not in the transcript."""
from __future__ import annotations

import re
from dataclasses import dataclass

from text_to_num import find_numbers  # pyright: ignore[reportMissingImports]

PREFIX = "(‹«[\"'„‚‘“”"
SUFFIX = ".,:;!?…›»)]\"'’‘“”"
SENTENCE_END = (".", "!", "?", "…")
PAUSE_S = .55
DE_ARTICLES = frozenset({"ein", "eine", "einen", "einem", "einer", "eines"})
DE_LARGE_SCALES = re.compile(r"milliard(?:e|en)|[a-z]*illion(?:en)?")
EN_LARGE_SCALES = frozenset({"million", "billion", "trillion", "quadrillion", "quintillion", "sextillion"})
DIGITS = {"de": "null eins zwei drei vier fünf sechs sieben acht neun".split(),
          "en": "zero one two three four five six seven eight nine".split()}
TEENS = {"de": "zehn elf zwölf dreizehn vierzehn fünfzehn sechzehn siebzehn achtzehn neunzehn".split(),
         "en": "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()}
TENS = {"de": "zwanzig dreißig vierzig fünfzig sechzig siebzig achtzig neunzig".split(),
        "en": "twenty thirty forty fifty sixty seventy eighty ninety".split()}
SCALES = {"de": {"hundert", "tausend"}, "en": {"hundred", "thousand"}}
DECIMAL_WORD = {"de": "komma", "en": "point"}
ASCII_DIGITS = re.compile(r"[0-9]+")


@dataclass
class Word:
    prefix: str
    core: str
    suffix: str
    start: float
    end: float
    number_text: str
    cut: bool = False
    literal_fraction: bool = False
    literal_integer: bool = False

    @classmethod
    def from_record(cls, record: dict, bounds: tuple[float, float]) -> Word:
        raw = record["w"].strip()
        left = raw.lstrip(PREFIX)
        core = left.rstrip(SUFFIX)
        start, end = record["s"], record["e"]
        return cls(raw[:len(raw) - len(left)], core.lower(), left[len(core):], start, end,
                   core.lower(), start < bounds[0] or end > bounds[1])

    def text(self) -> str:
        return self.number_text

    def nt_separated(self, previous: Word) -> bool:
        return bool(self.cut or previous.cut or self.prefix or previous.suffix or self.start - previous.end > PAUSE_S
                    or (self.literal_integer and previous.core not in {"minus", "plus"}))

    def not_a_number_part(self) -> bool:
        return False  # Required by the text2num token interface.


def cardinal_token(n: int, lang: str) -> str:
    """Spell 0–999 for the parser's mixed digit/word input, not for display."""
    if n < 10:
        return DIGITS[lang][n]
    if n < 20:
        return TEENS[lang][n - 10]
    if n < 100:
        tens, units = divmod(n, 10)
        word = TENS[lang][tens - 2]
        if not units:
            return word
        unit = DIGITS[lang][units]
        return ("ein" if units == 1 else unit) + "und" + word if lang == "de" else word + "-" + unit
    hundreds, tail = divmod(n, 100)
    unit = DIGITS[lang][hundreds]
    if lang == "de":
        return ("ein" if hundreds == 1 else unit) + "hundert" + (cardinal_token(tail, lang) if tail else "")
    return unit + "-hundred" + ("-" + cardinal_token(tail, lang) if tail else "")


def large_scale(word: str, lang: str) -> bool:
    return bool(DE_LARGE_SCALES.fullmatch(word)) if lang == "de" else word in EN_LARGE_SCALES


def prepare_mixed_tokens(tokens: list[Word], lang: str) -> None:
    """Give the word parser context for raw digits next to scales or decimals."""
    decimal = DECIMAL_WORD[lang]
    digit_words = set(DIGITS[lang]) | ({"ein", "zwo"} if lang == "de" else {"nought", "naught"})
    scales = SCALES[lang]

    def is_scale(word: str) -> bool:
        return word in scales or large_scale(word, lang)

    fractional = False
    for i, token in enumerate(tokens):
        previous = tokens[i - 1] if i else None
        next_word = tokens[i + 1] if i + 1 < len(tokens) else None
        if previous and token.nt_separated(previous):
            fractional = False
        if token.core == decimal:
            fractional = True
        elif ASCII_DIGITS.fullmatch(token.core):
            if fractional:
                # One proxy digit per timed token; restore all raw digits after parsing.
                token.number_text = DIGITS[lang][int(token.core[-1])]
                token.literal_fraction = True
            elif next_word and next_word.core == decimal and not next_word.nt_separated(token):
                # The value before the separator is already exact; keep it verbatim.
                token.number_text = DIGITS[lang][1]
                token.literal_integer = True
            elif next_word and is_scale(next_word.core) and not next_word.nt_separated(token):
                if len(token.core) <= 3 and int(token.core) > 0:
                    token.number_text = cardinal_token(int(token.core), lang)
                else:
                    # Don't reinterpret the suffix of an already numeric quantity.
                    for j in range(i + 1, len(tokens)):
                        if not is_scale(tokens[j].core) or tokens[j].nt_separated(tokens[j - 1]):
                            break
                        tokens[j].number_text = ""
            elif previous and not token.nt_separated(previous) and len(token.core) <= 3:
                after_scale = is_scale(previous.core)
                if lang == "en" and previous.core == "and" and i >= 2:
                    before_and = tokens[i - 2]
                    after_scale = is_scale(before_and.core) and not previous.nt_separated(before_and)
                if after_scale and int(token.core) > 0:
                    token.number_text = cardinal_token(int(token.core), lang)
        elif token.core not in digit_words:
            fractional = False


def display_words(words: list[dict], language: str, *,
                  bounds: tuple[float, float] = (-float("inf"), float("inf"))) -> list[dict]:
    """Merge recognized spans; retain their first start and last end for ASS."""
    lang = language.lower().split("-", 1)[0]
    if lang == "auto":
        lang = "de"
    if lang not in {"de", "en"} or not words:
        return words
    tokens = [Word.from_record(w, bounds) for w in words]
    if lang == "de":
        for current, following in zip(tokens, tokens[1:]):
            if (current.core in DE_ARTICLES and DE_LARGE_SCALES.fullmatch(following.core)
                    and not following.nt_separated(current)):
                current.number_text = "ein"
    prepare_mixed_tokens(tokens, lang)

    output: list[dict] = []
    cursor = 0
    for number in find_numbers(tokens, lang, threshold=0):
        start, end = number.start, number.end
        first, last = tokens[start], tokens[end - 1]
        previous = tokens[start - 1] if start else None
        if end - start == 1:
            # Articles and bare scales ("Millionen Fans") aren't exact numbers.
            if (lang == "de" and first.core in DE_ARTICLES or large_scale(first.core, lang)
                    or ASCII_DIGITS.fullmatch(first.core)):
                continue
            if lang == "en" and first.core == "second" and previous and not first.nt_separated(previous):
                preceding = output[-1]["w"] if output and start == cursor else previous.core
                if preceding in {"a", "one", "per", "every"} or preceding.isdecimal():
                    continue
        if end - start == 2 and ASCII_DIGITS.fullmatch(first.core) and large_scale(last.core, lang):
            continue  # "2 Millionen" is already a valid, compact rendering.

        value = number.text
        separator = "," if lang == "de" else "."
        span = tokens[start:end]
        if any(t.literal_integer or t.literal_fraction for t in span):
            divider = next((i for i, t in enumerate(span) if t.core == DECIMAL_WORD[lang]), None)
            integer, mark, fraction = value.partition(separator)
            if divider is None or not mark or number.is_ordinal:
                continue  # An incomplete mixed decimal is not a separate number.
            fraction_tokens = span[divider + 1:]
            if len(fraction_tokens) != len(fraction):
                continue  # Never guess how the library mapped tokens to digits.
            if first.literal_integer:
                integer = first.core
            fraction = "".join(t.core if t.literal_fraction else digit for t, digit in zip(fraction_tokens, fraction))
            value = integer + separator + fraction
        if (not number.is_ordinal and start > cursor and previous and previous.core in {"minus", "plus"}
                and not first.nt_separated(previous)):
            value = ("-" if previous.core == "minus" else "+") + value
            start -= 1
            first = previous
        suffix = last.suffix
        if value.endswith(".") and suffix.startswith("."):
            suffix = suffix[1:]
        output.extend(words[cursor:start])
        output.append({**words[start], "w": first.prefix + value + suffix, "e": last.end,
                       "caption_sentence_end": words[end - 1]["w"].rstrip().endswith(SENTENCE_END)})
        cursor = end
    output.extend(words[cursor:])
    return output
