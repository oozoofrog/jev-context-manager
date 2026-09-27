"""Load only the bundled stdlib runtime, never a workspace jcm module."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
if sys.argv[1:] == ['plugin-hook']:
    from jcm.plugin import hook_main
    raise SystemExit(hook_main())
from jcm.cli import main
raise SystemExit(main())
