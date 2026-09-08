"""Entry point for the frozen command.

PyInstaller runs its entry script as a top-level module, so `planbrain/cli.py`
cannot be it: that file lives in a package and uses relative imports, which are
correct there and fail with "attempted relative import with no known parent
package" when the file is executed directly.

So the frozen binary starts here and imports the package properly. `cli.py`
stays a module, `python -m planbrain.cli` keeps working, and the two entry
points -- console script and frozen exe -- run identical code.
"""

import sys

from planbrain.cli import main

if __name__ == "__main__":
    sys.exit(main())
