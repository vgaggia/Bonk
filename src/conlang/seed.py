"""Initial seed data for the conlang dictionary. Loaded once on first run when
data/conlang_dictionary.json doesn't yet exist."""

from __future__ import annotations

import hashlib
from typing import Dict, List

from .schema import (
    SCHEMA_VERSION,
    Conjugation,
    Entry,
    ExampleSentence,
    GrammarRule,
    Meta,
    now_iso,
)


def _slug_id(prefix: str, word: str, category: str = "") -> str:
    h = hashlib.sha1(f"{prefix}:{word}:{category}".encode("utf-8")).hexdigest()[:8]
    base = f"{prefix}-{word.lower()}"
    if category:
        base += f"-{category}"
    return f"{base}-{h}"


def _entry(
    word: str,
    category: str,
    meaning: str,
    confidence: str,
    plural: str | None = None,
    pronunciation: str | None = None,
    notes: str = "",
) -> Entry:
    return Entry(
        id=_slug_id("e", word, category),
        word=word,
        category=category,
        meaning=meaning,
        confidence=confidence,
        plural=plural,
        pronunciation=pronunciation,
        notes=notes,
    )


def _rule(rule: str, detail: str, confidence: str) -> GrammarRule:
    return GrammarRule(id=_slug_id("g", rule), rule=rule, detail=detail, confidence=confidence)


def _example(conlang: str, english: str, confidence: str) -> ExampleSentence:
    return ExampleSentence(
        id=_slug_id("s", conlang),
        conlang=conlang,
        english=english,
        confidence=confidence,
    )


def build_seed_entries() -> List[Entry]:
    entries: List[Entry] = []

    # Pronouns
    entries += [
        _entry("Ëi", "pronoun", "I (1st person singular)", "confirmed"),
        _entry("Te", "pronoun", "You (singular and plural)", "confirmed"),
        _entry("Ton", "pronoun", "He / She / It", "high"),
        _entry("Na", "pronoun", "We / They", "high"),
        _entry("Ta", "pronoun", "My (1st person possessive)", "confirmed"),
        _entry("Tëi", "pronoun", "Your (2nd person possessive)", "confirmed"),
        _entry("Jai", "pronoun", "His / Her / Its (3rd person possessive)", "high"),
    ]

    # Nouns
    entries += [
        _entry("Ka", "noun", "Penis", "confirmed", plural="Kon"),
        _entry("Texik", "noun", "Asshole", "confirmed", plural="Texikon"),
        _entry("Buana", "noun", "Friend", "confirmed", plural="Buanon"),
        _entry("Tamako", "noun", "Hello / Goodbye (greeting)", "high", plural="Tamakon"),
        _entry("Wubu", "noun", "Woman (?)", "guess", plural="Wubon"),
        _entry("Wuke", "noun", "Man (?)", "guess", plural="Wukon"),
        _entry("Ezuki", "noun", "Heart / Soul / Love (abstract positive)", "uncertain"),
        _entry("Beto", "noun", "Sexual act / body part", "uncertain"),
        _entry("Taki", "noun", "Unknown noun (people get hyped about it)", "unknown", plural="Takon"),
        _entry("Daka", "noun", "Unknown noun", "unknown", plural="Dakon"),
        _entry(
            "Ha",
            "noun",
            "Unknown noun (also serves as 'yes' particle — see other entry)",
            "unknown",
        ),
        _entry(
            "Ni",
            "noun",
            "Unknown noun (also serves as 'no/not' particle — see other entry)",
            "unknown",
        ),
        _entry("Eke", "noun", "Unknown noun", "unknown", plural="Ekon"),
        _entry("Pan", "noun", "Unknown noun", "unknown", plural="Panon"),
        _entry("Panke", "noun", "Unknown noun", "unknown", plural="Pankon"),
        _entry(
            "Wa",
            "noun",
            "Unknown noun (also serves as 'what' question word — see other entry)",
            "unknown",
        ),
        _entry(
            "We",
            "noun",
            "Unknown noun (also serves as 'why' question word — see other entry)",
            "unknown",
        ),
    ]

    # Verbs (infinitives — conjugations live in the conjugations dict)
    entries += [
        _entry("Skani", "verb", "To like", "confirmed", notes="Conjugates to Skan"),
        _entry("Itxi", "verb", "To kiss", "confirmed", notes="Conjugates to Itx"),
        _entry("Faki", "verb", "To fuck", "confirmed", notes="Conjugates to Fak"),
        _entry("Nimi", "verb", "To love", "confirmed", notes="Conjugates to Nim"),
        _entry("Ja", "verb", "To have", "confirmed", notes="Irregular: Ëi Ja, Te Ja, Ton Jau, Na Jaami"),
        _entry("Ob", "verb", "Hit / Do to / Give (?)", "uncertain"),
        _entry("Beti", "verb", "Unknown verb", "unknown", notes="Conjugates to Bet"),
        _entry("Kixixi", "verb", "Unknown verb", "unknown", notes="Conjugates to Kixix"),
    ]

    # Adjectives / Adverbs
    entries += [
        _entry("Wo", "adjective", "Big / Very", "confirmed"),
        _entry("Moi", "adjective", "Big / Very (synonymous with 'wo')", "confirmed"),
        _entry("Ti", "adjective", "Unknown adjective", "unknown"),
        _entry("Tuk", "adjective", "Good", "confirmed"),
    ]

    # Particles
    entries += [
        _entry("Ha", "particle", "Yes", "confirmed"),
        _entry("Ni", "particle", "No / Not (negation)", "confirmed"),
        _entry("Xa", "particle", "Is (copula)", "confirmed"),
        _entry("U", "particle", "And", "confirmed"),
        _entry("San", "particle", "Also (post-verb)", "confirmed"),
        _entry("Wa", "particle", "What (question word)", "confirmed"),
        _entry("We", "particle", "Why (question word)", "high"),
        _entry("Zo", "particle", "So / Then", "high"),
    ]

    # Emotions / slang
    entries += [
        _entry("Mubu", "emotion", "Happy 😃", "confirmed"),
        _entry("Mibi", "emotion", "Angry 😠", "confirmed"),
        _entry("Dingding", "slang", "Horny / Freaky", "high"),
        _entry("Staki", "emotion", "Unknown emotion", "uncertain"),
    ]

    return entries


def build_seed_grammar_rules() -> List[GrammarRule]:
    return [
        _rule("Pronunciation", "X is pronounced as 'sh'", "confirmed"),
        _rule("Word order", "Subject-Verb-Object (SVO)", "confirmed"),
        _rule("Adjective placement", "Adjectives come before nouns", "confirmed"),
        _rule("Adverb placement", "The adverb 'san' (also) is post-verb", "confirmed"),
        _rule("Plural formation", "Plurals add -on or -n suffix", "confirmed"),
        _rule(
            "Verb conjugation (regular)",
            "Drop the final -i; most verbs use the same form for Ëi and Te",
            "confirmed",
        ),
        _rule(
            "Verb conjugation (irregular: Ja)",
            "Ja → Ëi Ja, Te Ja, Ton Jau, Na Jaami",
            "confirmed",
        ),
        _rule(
            "Question construction",
            "'Wa xa [X]?' = 'What is X?'",
            "confirmed",
        ),
        _rule("Negation", "'Ni' is placed before the verb", "confirmed"),
    ]


def build_seed_examples() -> List[ExampleSentence]:
    return [
        _example(
            "Ëi skan itxi tëi texik u ëi skan san faki tëi wo ka",
            "I like kissing your asshole and I also like fucking your big penis",
            "confirmed",
        ),
        _example("Nim te", "Love you", "confirmed"),
        _example("Ëi skan te", "I like you", "confirmed"),
        _example("Tëi ezuki xa wo tuk", "Your ezuki is very good", "confirmed"),
        _example("Tëi ka xa wo wo", "Your penis is very very big", "confirmed"),
        _example("Ëi ja wo 💩", "I have a big 💩", "confirmed"),
        _example("Wo tuk wuke", "Very good man/woman", "confirmed"),
        _example("Blui xa wo mibi", "Blui is very angry", "confirmed"),
        _example("Tamako buanon", "Hello/Goodbye friends", "confirmed"),
        _example("Te buana", "You friend (singular)", "confirmed"),
        _example("Te buanon", "You friends (plural)", "confirmed"),
    ]


def build_seed_conjugations() -> Dict[str, Conjugation]:
    return {
        "Beti": Conjugation(infinitive="Beti", forms={"Ëi": "Bet", "Te": "Bet"}),
        "Itxi": Conjugation(infinitive="Itxi", forms={"Ëi": "Itx", "Te": "Itx"}),
        "Faki": Conjugation(infinitive="Faki", forms={"Ëi": "Fak", "Te": "Fak"}),
        "Skani": Conjugation(infinitive="Skani", forms={"Ëi": "Skan", "Te": "Skan"}),
        "Kixixi": Conjugation(infinitive="Kixixi", forms={"Ëi": "Kixix", "Te": "Kixix"}),
        "Nimi": Conjugation(infinitive="Nimi", forms={"Ëi": "Nim", "Te": "Nim"}),
        "Ja": Conjugation(
            infinitive="Ja",
            forms={"Ëi": "Ja", "Te": "Ja", "Ton": "Jau", "Na": "Jaami"},
            notes="Irregular",
        ),
    }


def build_seed_meta() -> Meta:
    return Meta(
        schema_version=SCHEMA_VERSION,
        last_updated=None,
        last_message_id=None,
        sync_interval_hours=6.0,
        auto_sync_enabled=True,
        force_sync_requested_at=None,
        total_entries=0,
        confirmed_count=0,
        recent_context=[],
    )


def build_full_seed() -> Dict:
    """Return a JSON-serializable dict ready to be written to disk on first run."""
    entries = build_seed_entries()
    rules = build_seed_grammar_rules()
    examples = build_seed_examples()
    conjugations = build_seed_conjugations()
    meta = build_seed_meta()

    confirmed_count = sum(1 for e in entries if e.confidence == "confirmed")
    meta.total_entries = len(entries)
    meta.confirmed_count = confirmed_count

    return {
        "meta": meta.to_dict(),
        "entries": [e.to_dict() for e in entries],
        "grammar_rules": [r.to_dict() for r in rules],
        "example_sentences": [e.to_dict() for e in examples],
        "conjugations": {k: v.to_dict() for k, v in conjugations.items()},
    }
