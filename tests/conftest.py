import sys
from pathlib import Path

# The plugin is a plain package inside plugins/trismegistos/; import it as `routing` without Hermes.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "trismegistos"))
