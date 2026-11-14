"""The server's user-facing messages in other languages.

Messages are written in English in the code. Each file of archiver/locales,
named after a language (fr.json), gives their translation: the English message
as it is written in the code, then its version in that language; "{name}"
stands for a part that varies and is kept as it is. To add a language, copy
fr.json under the new language's code and translate its values; the
interface's own texts are in web/locales.

Messages are translated when a response is sent, in the language the interface
asks for, so an error stored with an archive follows a later change of language.
A message that is in no file, such as raw yt-dlp output, is sent as is."""
import json
import re
from pathlib import Path

LOCALES = Path(__file__).resolve().parent / "locales"
WEB = Path(__file__).resolve().parent.parent / "web"
SOURCE = "en"


def compile_template(template):
    seen = set()

    def part(match):
        # A part named twice in a message is the same text both times.
        name = match[1]
        if name in seen:
            return f"(?P={name})"
        seen.add(name)
        return f"(?P<{name}>.+?)"

    return re.compile("^" + re.sub(r"\\\{(\w+)\\\}", part, re.escape(template)) + "$", re.S)


def load():
    """The translations of each language file: (pattern of the English message, its translation) pairs."""
    return {path.stem: [(compile_template(english), translation) for english, translation in json.loads(path.read_text(encoding="utf-8")).items()]
            for path in sorted(LOCALES.glob("*.json"))}


CATALOGS = load()


def translate(text, lang):
    for pattern, translation in CATALOGS.get(lang, ()):
        if match := pattern.match(text):
            # Parts of a message may be messages themselves ("… : {error}").
            return translation.format(**{key: translate(value, lang) for key, value in match.groupdict().items()})
    return text


def interface_script():
    """web/i18n.js as browsers get it: with every language of web/locales in its table, English first."""
    languages = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(
        (WEB / "locales").glob("*.json"), key=lambda path: (path.stem != "en", path.stem))}
    table = json.dumps(languages, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (WEB / "i18n.js").read_text(encoding="utf-8").replace("/*__LOCALES__*/{}", table, 1)


def language(header, accept_language):
    """The interface's language: its explicit choice, else the browser's; English when it has no file."""
    choice = re.split(r"[-_,;]", (header or accept_language or "").strip().lower())[0]
    return choice if choice in CATALOGS else SOURCE


def localize(data, lang):
    """Translate every "error" message in a JSON response."""
    if lang == SOURCE:
        return data
    if isinstance(data, dict):
        return {key: translate(value, lang) if key == "error" and isinstance(value, str) else localize(value, lang)
                for key, value in data.items()}
    if isinstance(data, list):
        return [localize(value, lang) for value in data]
    return data
