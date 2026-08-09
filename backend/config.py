import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    MONGODB_URI = os.environ.get("MONGODB_URI", "")
    FLASK_ENV = os.environ.get("FLASK_ENV", "production")
    PORT = int(os.environ.get("PORT", "5000"))
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
    DEBUG = FLASK_ENV == "development"
