import os

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

SECRET_KEY = os.getenv("SECRET_KEY", "texas-holdem-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

DEFAULT_SMALL_BLIND = int(os.getenv("DEFAULT_SMALL_BLIND", 10))
DEFAULT_BIG_BLIND = int(os.getenv("DEFAULT_BIG_BLIND", 20))
DEFAULT_TURN_TIMEOUT = int(os.getenv("DEFAULT_TURN_TIMEOUT", 30))
MAX_PLAYERS = int(os.getenv("MAX_PLAYERS", 9))
MIN_PLAYERS = 2

SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8888))
