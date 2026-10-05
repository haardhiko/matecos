"""
importer
========
Universal multi-language tool import pipeline for MATECOS.
"""

from src.tools.importer.detectors import detect_project_metadata
from src.tools.importer.discovery import discover_candidates
from src.tools.importer.models import DiscoveredCandidate, ImportReport, ToolImportError
from src.tools.importer.pipeline import UniversalImporter

__all__ = [
    "UniversalImporter",
    "ImportReport",
    "DiscoveredCandidate",
    "ToolImportError",
    "detect_project_metadata",
    "discover_candidates",
]
