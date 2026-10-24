from pathlib import Path

from uap_observer.fixtures import generate

if __name__ == "__main__":
    print(generate(Path(__file__).resolve().parents[1] / "artifacts/fixtures"))
