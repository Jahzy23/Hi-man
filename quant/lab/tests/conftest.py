import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # .../quant
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.dont_write_bytecode = True                      # tests import strategies/: leave no .pyc behind there
