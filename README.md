# Heima (Home Assistant Integration)

Heima is an intent-driven home intelligence engine for Home Assistant.

## Project Icon
![Heima icon](docs/assets/heima-icon.svg)

## Development status
This repository currently contains an implementation skeleton aligned with the specs in `docs/specs/`.

## Install (HACS custom repo)
- Add this repository as a custom repository in HACS (Integration)
- Install **Heima**
- Restart Home Assistant
- Add integration: Settings → Devices & services → Add integration → Heima

## Specs
See `docs/specs/INDEX.md` and the versioned spec files.

## Development
- Install dev dependencies: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
- Run the current automated suite: `.venv/bin/pytest -q`
- The HA integration-test harness (`pytest-homeassistant-custom-component`) owns the compatible `pytest` / `pytest-asyncio` versions for this repo's Home Assistant line, so we do not pin those separately in `requirements-dev.txt`.
