# Translations

IP Ban Manager ships translations for Home Assistant's normal integration UI and for the custom live panel.

## Files

| Path | Purpose |
| --- | --- |
| `custom_components/ip_ban_manager/strings.json` | English source strings used by Home Assistant for config flow, options flow, repairs, selectors, exceptions, and entities. |
| `custom_components/ip_ban_manager/translations/en.json` | English Home Assistant locale file generated from the same source content. |
| `custom_components/ip_ban_manager/translations/*.json` | Localized Home Assistant locale files. These are loaded by Home Assistant. |
| `custom_components/ip_ban_manager/panel_translations/en.json` | English source strings for the custom IP Ban Manager panel. |
| `custom_components/ip_ban_manager/panel_translations/*.json` | Localized panel strings loaded by IP Ban Manager's panel API. |
| `scripts/translation_overlays/*.json` | Per-language overlay files used to generate the shipped Home Assistant and panel locale files. |
| `scripts/ban_file_health_translations.json` | Per-language health strings for ban-file repair details. |

The panel strings are intentionally split out of Home Assistant's normal translation files. Home Assistant should only see supported `strings.json` / `translations/*.json` sections, while the custom panel receives its own flattened translation payload from `/api/ip_ban_manager/status`.

## Supported Locales

English is the source locale. The generated non-English locale set is:

`ca`, `cs`, `da`, `de`, `el`, `es`, `fi`, `fr`, `hu`, `it`, `ja`, `ko`, `nb`, `nl`, `pl`, `pt`, `pt-BR`, `ro`, `ru`, `sk`, `sv`, `tr`, `uk`, `zh-Hans`, and `zh-Hant`.

If a signed-in Home Assistant user has another language selected, the panel and Home Assistant integration UI fall back to English.

## Runtime Loading

Home Assistant loads `strings.json` and `translations/*.json` through its normal translation system.

The custom panel uses `custom_components/ip_ban_manager/i18n.py`:

- `async_normalize_language()` resolves Home Assistant locale tags such as `pt_BR`, `pt-BR`, or `pt` to the best shipped file.
- `async_load_panel_translations()` loads and flattens panel strings without blocking the Home Assistant event loop.
- `load_panel_translations()` always merges localized strings over English, so missing localized panel strings fall back to English instead of showing raw keys.
- `async_load_health_issue_strings()` does the same for health/repair text used by the panel status API.

## Maintenance Workflow

When English text changes:

1. Update `custom_components/ip_ban_manager/strings.json` for Home Assistant UI text.
2. Update `custom_components/ip_ban_manager/panel_translations/en.json` for custom panel text.
3. Update each matching `scripts/translation_overlays/*.json` entry.
4. Run:

```bash
python scripts/build_translations.py
```

The generator deep-merges each overlay over English, writes both translation trees, applies the ban-file health translations, and verifies that every generated locale includes every English string leaf.

## Audit Checklist

Before release, verify:

- Every JSON file parses.
- Every locale has the same string-key set as English.
- Every localized string preserves the same `{placeholder}` names as English.
- New feature prose is actually localized instead of being copied from English solely to satisfy key parity.
- `translations/*.json` does not contain a top-level `panel` section.
- `panel_translations/*.json` contains panel-only strings.
- Regenerating translations with `scripts/build_translations.py` produces no unexpected diff.
- Product names and intentionally borrowed UI nouns are allowed to remain in English.

Current structural audit result:

- `translations/*.json`: all 25 non-English locales match the English key set and placeholders.
- `panel_translations/*.json`: all 25 non-English locales match the English key set and placeholders.
- Public Region Lock, callback protection, regional login thresholds, and NGINX Proxy Manager controls are localized in every shipped panel locale.
- Regeneration from overlays completed cleanly with no file changes.

## Acceptable English Carry-Through

Some strings are intentionally unchanged across locales:

- `IP Ban Manager` is the product name.
- `localhost` is a technical term in the safe-default option.
- `[%key:common::config_flow::error::unknown%]` is a Home Assistant common translation reference.
- Short source labels such as `Panel`, `Service`, `YAML`, `Download`, or `Upload` may remain English in a few locales where the term is commonly used or the overlay has not provided a better local word.

These should not be treated as structural translation failures.
