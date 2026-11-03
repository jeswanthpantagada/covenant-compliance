import hashlib
import os
from pathlib import Path
from typing import Union


def compute_file_sha256(file_path: Union[str, Path]) -> str:
    """Computes the SHA-256 checksum of a file on disk."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_file_integrity(file_path: Union[str, Path], expected_sha256: str) -> bool:
    """Verifies that a file matches its expected SHA-256 checksum."""
    actual_hash = compute_file_sha256(file_path)
    return actual_hash.lower() == expected_sha256.lower()
