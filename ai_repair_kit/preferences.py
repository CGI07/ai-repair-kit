"""Read and write the application's own preferences."""

from PySide6.QtCore import QSettings

THEME_OPTIONS = ("light", "dark")


def read_choice(settings: QSettings, key: str, options: tuple[str, ...]) -> str:
    value = settings.value(key)
    return value if isinstance(value, str) and value in options else options[0]


def save_preferences(settings: QSettings, language: str, theme: str) -> bool:
    settings.setValue("language", language)
    settings.setValue("theme", theme)
    settings.sync()
    return settings.status() == QSettings.Status.NoError
