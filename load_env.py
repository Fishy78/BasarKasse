#!/usr/bin/env python
"""
Load environment variables from .env.production file
"""
import os
from pathlib import Path

def load_env_file(env_file=None):
    """Load environment variables from a file"""
    if env_file is None:
        env_file = Path(__file__).parent / ".env.production"
    
    if not env_file.exists():
        return False
    
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not os.environ.get(key):  # Nicht überschreiben wenn bereits gesetzt
                    os.environ[key] = value
    
    return True

if __name__ == "__main__":
    if load_env_file():
        print("✓ Environment variables loaded from .env.production")
        print("\nLoaded variables:")
        for key in ["DJANGO_DEBUG", "DATABASE_URL", "DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS"]:
            value = os.environ.get(key, "NOT SET")
            if key == "DJANGO_SECRET_KEY":
                value = f"{value[:10]}...{value[-10:]}" if len(value) > 20 else "***"
            if key == "DATABASE_URL":
                value = value.split("@")[-1] if "@" in value else value
            print(f"  {key}: {value}")
    else:
        print("✗ No .env.production file found")
        print("Create one based on .env.production.example")
