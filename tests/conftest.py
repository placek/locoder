import sys
from pathlib import Path

# The plugin is a plain package inside plugins/locoder/; import it as `routing` without Hermes.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "locoder"))
