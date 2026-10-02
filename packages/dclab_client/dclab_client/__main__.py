"""``python -m dclab_client`` runs the customer CLI."""

import sys

from dclab_client.cli import main

if __name__ == "__main__":
    sys.exit(main())
