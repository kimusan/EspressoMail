"""SQLite persistence and local email store for EspressoMail."""

from espressomail.storage.database import (
    LocalStore,
    create_sample_ppm_image,
    default_data_dir,
)

__all__ = [
    "LocalStore",
    "create_sample_ppm_image",
    "default_data_dir",
]
