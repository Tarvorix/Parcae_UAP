import argparse
import json
from pathlib import Path

from uap_observer.evidence.verify import verify_package

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    result = verify_package(parser.parse_args().package)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["verified"] else 1)
