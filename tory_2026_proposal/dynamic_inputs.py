# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Run the behavioural scenarios: the upsizing (portfolio shift) and "less avoidance" figures in the article.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "residence_codex"):
    if _p.is_dir():
        sys.path.insert(0, str(_p))

from behaviour import main  # noqa: E402

if __name__ == "__main__":
    main()
