"""Resolve UI language and manage the active Qt translation catalog."""

from pathlib import Path

from PySide6.QtCore import QLocale, QTranslator
from PySide6.QtWidgets import QApplication

# Add a locale code and a matching app_<code>.qm to support another language.
LANGUAGE_NAMES = {"en": "English", "ko": "한국어"}
LANGUAGE_OPTIONS = ("system", *LANGUAGE_NAMES)
CATALOG_DIR = Path(__file__).with_name("translations")


def resolve_language(choice: str, system_locale: QLocale) -> str:
    if choice != "system":
        return choice if choice in LANGUAGE_OPTIONS[1:] else "en"

    locale_name = system_locale.name()
    for code in (locale_name, locale_name.split("_", 1)[0]):
        if code in LANGUAGE_OPTIONS[1:]:
            return code
    return "en"


class Translations:
    def __init__(self, app: QApplication, catalog_dir: Path = CATALOG_DIR) -> None:
        self.app = app
        self.catalog_dir = catalog_dir
        self.translator = QTranslator(app)

    def apply(self, choice: str) -> bool:
        language = resolve_language(choice, QLocale.system())
        self.app.removeTranslator(self.translator)

        loaded = language == "en"
        if language != "en":
            catalog = self.catalog_dir / f"app_{language}.qm"
            loaded = self.translator.load(str(catalog)) and self.app.installTranslator(
                self.translator
            )

        effective_locale = QLocale(language if loaded else "en")
        self.app.setLayoutDirection(effective_locale.textDirection())
        return loaded
