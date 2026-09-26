"""Defuddle extract backend — clean article extraction via the ``defuddle`` Node CLI.

Hermes' other extract backends return whatever the vendor's HTML-to-text pass produced,
which on a typical page still carries nav, sidebars, cookie banners and footer link farms.
Defuddle (https://github.com/kepano/defuddle — the engine behind Obsidian Web Clipper)
strips a page down to its main content, so ``web_extract`` hands the model an article
instead of a chrome-wrapped dump: fewer tokens, less distraction, and quotes that actually
come from the text rather than a sidebar.

Extract-only by design. ``supports_search()`` is False, so the search walk never routes
here; pair it with any search backend (keenable, tavily, ...) via::

    web:
      extract_backend: defuddle

Failure policy: per-URL problems come back as error entries, never exceptions. If the
WHOLE batch fails (binary missing, Node broken, network down) Hermes' one-shot keyless
rescue retries that call on the keyless ring — so pinning this backend cannot take
``web_extract`` down, it only changes who serves the happy path.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugins.web._common import BaseWebSearchProvider, document, page_error, run_extract, setup_schema

logger = logging.getLogger(__name__)

# Per-URL wall-clock cap. Defuddle fetches the page itself, so this covers network + parse.
_DEFAULT_TIMEOUT_S = 60
# Pages are fetched concurrently; small because each worker is a Node process.
_MAX_WORKERS = 4
# Checked after $HERMES_DEFUDDLE_BIN / web.defuddle_bin / $PATH. Matches the install in README.
_VENDORED_BIN = Path.home() / ".hermes" / "tools" / "defuddle" / "node_modules" / ".bin" / "defuddle"

# Sites that fingerprint the default Node fetch UA answer 403 (Medium, many CDN-fronted blogs);
# defuddle documents --user-agent as the remedy. Overridable via web.defuddle_user_agent.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

_INSTALL_HINT = (
    "defuddle CLI not found. Install it with:\n"
    "  mkdir -p ~/.hermes/tools/defuddle && cd ~/.hermes/tools/defuddle && npm install defuddle\n"
    "or put `defuddle` on PATH, or set web.defuddle_bin / $HERMES_DEFUDDLE_BIN."
)


def _web_config() -> Dict[str, Any]:
    """The ``web`` section of config.yaml, or ``{}`` (never raises — runs on every paint)."""
    try:
        from hermes_cli.config import load_config_readonly

        cfg = (load_config_readonly() or {}).get("web")
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001 — config layer is optional here
        return {}


def _resolve_bin() -> Optional[str]:
    """Locate the defuddle executable. Cheap (stat only, no exec) — called by is_available()
    at tool-registration time and on every ``hermes tools`` paint."""
    candidates = [
        os.environ.get("HERMES_DEFUDDLE_BIN", "").strip(),
        str(_web_config().get("defuddle_bin", "") or "").strip(),
    ]
    for candidate in candidates:
        if candidate:
            expanded = os.path.expanduser(os.path.expandvars(candidate))
            if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
                return expanded
            logger.debug("Configured defuddle bin %r is not executable; continuing search", candidate)
    if found := shutil.which("defuddle"):
        return found
    if _VENDORED_BIN.is_file() and os.access(_VENDORED_BIN, os.X_OK):
        return str(_VENDORED_BIN)
    return None


def _timeout_seconds() -> int:
    try:
        return max(1, int(_web_config().get("defuddle_timeout", _DEFAULT_TIMEOUT_S)))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def _user_agent() -> str:
    """UA sent by defuddle's fetch. Empty string in config disables the flag entirely."""
    raw = _web_config().get("defuddle_user_agent", _DEFAULT_USER_AGENT)
    return "" if raw is None else str(raw)


def _subprocess_env() -> Dict[str, str]:
    """Sanitized child env when Hermes exposes its helper, else the inherited env."""
    env = dict(os.environ)
    try:
        from tools.environments.local import _sanitize_subprocess_env

        return _sanitize_subprocess_env(env)
    except Exception:  # noqa: BLE001 — helper is internal; inheriting is an acceptable fallback
        return env


def _parse_one(binary: str, url: str, want_html: bool, timeout: int) -> Dict[str, Any]:
    """Run ``defuddle parse`` for one URL and normalize it into an extract entry.

    ``--json`` is always requested so the title and metadata come from defuddle's own parse
    rather than a guess; ``--markdown`` additionally converts the extracted body.
    """
    cmd = [binary, "parse", url, "--json"]
    if not want_html:
        cmd.append("--markdown")
    if agent := _user_agent():
        cmd += ["--user-agent", agent]
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=_subprocess_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return page_error(url, f"defuddle timed out after {timeout}s")
    except OSError as exc:
        return page_error(url, f"defuddle could not be executed: {exc}")

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return page_error(url, f"defuddle failed (exit {proc.returncode}): {detail[-1] if detail else 'no output'}")

    raw = (proc.stdout or "").strip()
    if not raw:
        return page_error(url, "defuddle returned no output")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Defuddle emitted a body but not JSON — serve the text rather than losing the fetch.
        return document(url, "", raw)
    if not isinstance(parsed, dict):
        return page_error(url, "defuddle returned an unexpected JSON shape")

    content = str(parsed.get("content") or "").strip()
    if not content:
        return page_error(url, "defuddle extracted no main content from this page")
    title = str(parsed.get("title") or "")
    entry = document(url, title, content)
    # Surface the bibliographic fields the research skill cites, when the page carried them.
    extra = {k: parsed.get(k) for k in ("author", "published", "domain", "wordCount") if parsed.get(k)}
    if extra:
        entry["metadata"].update(extra)
    return entry


class DefuddleExtractProvider(BaseWebSearchProvider):
    """Main-content extraction through the defuddle CLI. No API key, no network service."""

    NAME = "defuddle"
    DISPLAY_NAME = "Defuddle (main-content extract)"
    EXTRACT = True

    def is_available(self) -> bool:
        """True when the defuddle binary is present. Stat-only, no exec, no network."""
        return _resolve_bin() is not None

    def supports_search(self) -> bool:
        """Extract-only backend — never let the search walk route here."""
        return False

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Fetch and clean each URL. Per-URL failures are entries, not exceptions."""
        want_html = str(kwargs.get("format") or "").lower() == "html"

        def _body() -> List[Dict[str, Any]]:
            binary = _resolve_bin()
            if binary is None:
                # Whole-batch failure → Hermes retries this call on the keyless ring.
                return [page_error(u, _INSTALL_HINT) for u in urls]
            timeout = _timeout_seconds()
            logger.info("Defuddle extract: %d URL(s), timeout=%ds", len(urls), timeout)
            if len(urls) == 1:
                return [_parse_one(binary, urls[0], want_html, timeout)]
            with cf.ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(urls))) as pool:
                return list(pool.map(lambda u: _parse_one(binary, u, want_html, timeout), urls))

        return run_extract("Defuddle", logger, urls, _body, verbatim_value_error=False)

    def get_setup_schema(self) -> Dict[str, Any]:
        return setup_schema(
            "Defuddle (main-content extract)",
            "free · no key · extract only",
            "Strips pages to their main content with the defuddle CLI (pair with any search backend)",
        )
