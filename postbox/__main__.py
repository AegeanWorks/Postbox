"""Allow ``python -m postbox.cli`` to run the CLI as documented."""

from postbox.cli import main

raise SystemExit(main())
