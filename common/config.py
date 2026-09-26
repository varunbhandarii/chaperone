import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


KEY_ID = "chaperone-agent-1"
KEYS_DIR = ROOT / "keys"
PRIVATE_KEY_PATH = KEYS_DIR / "agent_ed25519.pem"
JWKS_PATH = ROOT / "relay" / "jwks.json"


def merchant_public_url() -> str:
    return env("MERCHANT_PUBLIC_URL", "http://127.0.0.1:8002").rstrip("/")


def decisions_path() -> Path:
    return Path(env("DECISIONS_PATH", str(ROOT / "sessions" / "decisions.json")))


def ledger_path() -> Path:
    return Path(env("LEDGER_PATH", str(ROOT / "sessions" / "live.jsonl")))
