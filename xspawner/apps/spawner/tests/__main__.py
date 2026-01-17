import os
import sys
import unittest
from xspawner import APP_DIR

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arguments = sys.argv[1:]
        print("Arguments: ", arguments)
        os.environ["SERVER"] = sys.argv[1]

        loader = unittest.TestLoader()
        suite = loader.discover(start_dir=f"{APP_DIR}/spawner/tests")
        runner = unittest.TextTestRunner(failfast=True)
        result = runner.run(suite)
        print("Result: ", result)
    else:
        print("Usage: python3 -m {} <addr>".format(__package__))
        sys.exit(1)