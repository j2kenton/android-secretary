"""Nothing private in the code: the repository is public, and everything personal
(names, numbers, email addresses, groups, the owner's own rules) belongs in the
git-ignored private.toml and rules.toml.

This checks every tracked file against private.toml: the people, groups, numbers and
email addresses in it, and the extra words in its [public_check] terms. Whole words,
any case. Without a private.toml (a fresh clone) there's nothing to check against.
"""

import re
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "private.toml"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
NUMBER = re.compile(r"\+?\d[\d\s-]{6,}\d")


def _values(x) -> list[str]:
    """The leaf values (not the keys, which are often generic: "label", "news")."""
    if isinstance(x, dict):
        return [s for v in x.values() for s in _values(v)]
    if isinstance(x, list):
        return [s for v in x for s in _values(v)]
    return [str(x)]


def private_terms(private: dict, raw: str) -> tuple[set[str], set[str]]:
    """(words and names, numbers as their last 9 digits) that mustn't be public."""
    terms = set(private.get("public_check", {}).get("terms", []))
    terms |= set(private.get("people", {}))
    for name, p in private.get("urgent", {}).get("people", {}).items():
        terms |= {name, p.get("label", "")}
    school = private.get("school", {})
    terms |= set(school.get("groups", [])) | {school.get("forward_to", "")} | set(school.get("labels", {}))
    terms |= set(school.get("forward", {})) | set(_values(school.get("forward", {})))     # its keys are group names
    terms |= set(school.get("approval", {}).get("match", []))
    groups = private.get("groups", {})
    terms |= set(_values(groups.get("forward", {})))
    for name, lst in private.get("email_lists", {}).items():
        terms |= {name, lst.get("label", ""), lst.get("name", ""), lst.get("owner", "")}
    terms |= set(private.get("secretary", {}).get("greetings_only", []))
    cc = private.get("calendar_copy", {})
    terms |= {cc.get("personal", ""), cc.get("family", "")} | set(cc.get("skip", []))
    terms |= set(EMAIL.findall(raw))
    terms = {t.strip() for t in terms if len(t.strip()) >= 3}
    numbers = {re.sub(r"\D", "", n)[-9:] for n in NUMBER.findall(raw) if len(re.sub(r"\D", "", n)) >= 9}
    return terms, numbers


PUBLIC_ON_PURPOSE = {"LICENSE"}       # names the copyright holder, as a licence must


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / f for f in out.split("\0") if f and f not in PUBLIC_ON_PURPOSE]


def leaks(terms: set[str], numbers: set[str], files: list[Path]) -> list[str]:
    def pattern(t: str) -> str:
        # Hebrew joins little words on at the front ("בגבעת" = "in Givat"): allow up to two of them.
        before = r"(?<![\w])[בהוכלמש]{0,2}" if re.match(r"[\u0590-\u05FF]", t) else r"(?<!\w)"
        return rf"{before}{re.escape(t)}(?!\w)"
    word = re.compile("|".join(pattern(t) for t in sorted(terms, key=len, reverse=True)), re.I) if terms else None
    found = []
    for f in files:
        rel = f.relative_to(ROOT)
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate([str(rel), *text.splitlines()]):
            where = f"{rel}:{i}" if i else f"{rel} (file name)"
            # Names in code too: "test_smith_x" or "SMITH_NEW" (an underscore joins words there).
            if word and (m := word.search(line) or word.search(line.replace("_", " "))):
                found.append(f"{where}: {m.group(0)!r}")
            for n in NUMBER.findall(line):
                if re.sub(r"\D", "", n)[-9:] in numbers:
                    found.append(f"{where}: a phone number from private.toml")
    return found


class NoPrivateTests(unittest.TestCase):
    def test_no_private_names_numbers_or_addresses_in_tracked_files(self):
        if not PRIVATE.exists():
            self.skipTest("no private.toml: nothing to check against")
        raw = PRIVATE.read_text(encoding="utf-8")
        terms, numbers = private_terms(tomllib.loads(raw), raw)
        found = leaks(terms, numbers, tracked_files())
        self.assertEqual(found, [], "private details in tracked files:\n" + "\n".join(found))

    def test_private_files_are_ignored(self):
        for f in ("private.toml", "rules.toml", "var/state.json"):
            r = subprocess.run(["git", "check-ignore", "-q", f], cwd=ROOT)
            self.assertEqual(r.returncode, 0, f"{f} must be git-ignored")

    def test_the_check_finds_what_it_should(self):
        d = Path(__file__).resolve().parent
        terms, numbers = private_terms({"people": {"Zelda": "Z"}, "public_check": {"terms": ["Ruritania"]}},
                                       "phone = \"+972 50-765-4321\"\n")
        tmp = d / "_leak_probe.txt"
        try:
            tmp.write_text("hi zelda\nfrom RURITANIA\ncall 050-7654321\nZeldas and Ruritanian are fine\n"
                           "בזלדה\nזלדהו is fine\ndef test_zelda_case(): pass\n", encoding="utf-8")
            found = leaks(terms | {"זלדה"}, numbers, [tmp])
        finally:
            tmp.unlink()
        self.assertEqual(len(found), 5, found)


if __name__ == "__main__":
    unittest.main()
