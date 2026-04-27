"""Helpers for safely updating provider-related values in the .env file."""

import os
from filelock import FileLock

# Global lock for the .env file
env_lock = FileLock(".env.lock")

class EnvFileManager:
    """Serialize .env reads and writes behind a file lock."""

    @staticmethod
    def _read_env_lines() -> list[str]:
        """Read the current .env file into memory, if it exists."""
        if not os.path.exists(".env"):
            return []
        with open(".env") as f:
            return f.readlines()

    @staticmethod
    def _write_env_lines(lines: list[str]) -> None:
        """Persist the provided .env lines back to disk."""
        with open(".env", "w") as f:
            f.writelines(lines)

    @classmethod
    def update_env_variable(cls, key: str, value: str) -> None:
        """Create or replace a single environment variable in the .env file."""
        with env_lock:
            lines = cls._read_env_lines()
            new_lines = []
            found = False
            for line in lines:
                if line.strip().startswith(f"{key}="):
                    new_lines.append(f"{key}={value}\n")
                    found = True
                else:
                    new_lines.append(line)

            if not found:
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines[-1] += "\n"
                new_lines.append(f"{key}={value}\n")

            cls._write_env_lines(new_lines)

    @classmethod
    def remove_env_variable(cls, key: str) -> None:
        """Remove a single environment variable from the .env file if present."""
        with env_lock:
            lines = cls._read_env_lines()
            new_lines = [line for line in lines if not line.strip().startswith(f"{key}=")]
            cls._write_env_lines(new_lines)
