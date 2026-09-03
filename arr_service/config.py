"""Instance configuration.

Instances are declared as matching <NAME>_URL / <NAME>_API_KEY pairs in the
environment or in a .env file beside this project. The name prefix selects the
app type, so SONARR_4K_URL and RADARR_URL both work.
"""

import os
import re
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# Longest prefixes first so SONARR is not shadowed by a shorter match later.
APP_PREFIXES = (("SONARR", "sonarr"), ("RADARR", "radarr"))


class ConfigError(Exception):
    pass


def load_env(path=ENV_FILE):
    """Merge a .env file into a copy of os.environ. Real env vars win."""
    values = {}
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip("'\"")
    values.update(os.environ)
    return values


def app_type_for(name):
    for prefix, app in APP_PREFIXES:
        if name == prefix or name.startswith(prefix + "_"):
            return app
    return None


def discover(env=None):
    """Return [{name, app, url, api_key}] for every complete instance pair."""
    env = env if env is not None else load_env()
    instances = []
    problems = []

    for key in sorted(env):
        match = re.fullmatch(r"(.+)_URL", key)
        if not match:
            continue
        name = match.group(1)
        app = app_type_for(name)
        if app is None:
            continue

        url = (env.get(key) or "").strip()
        api_key = (env.get(f"{name}_API_KEY") or "").strip()
        if not url:
            continue
        if not api_key:
            problems.append(f"{name}: {name}_URL is set but {name}_API_KEY is empty")
            continue
        if not url.startswith(("http://", "https://")):
            problems.append(f"{name}: URL must start with http:// or https:// (got {url!r})")
            continue

        instances.append(
            {"name": name.lower(), "app": app, "url": url.rstrip("/"), "api_key": api_key}
        )

    return instances, problems
