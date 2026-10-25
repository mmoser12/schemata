"""Schemata merger: read, profile, match headers, fix units and dates, merge, emit clean.py."""

from .codegen import generate_clean_script, same_script
from .plan import MergePlan, apply_plan, build_plan, column_previews
from .profiler import head_records, profile_file
from .readers import SUPPORTED_SUFFIXES, UnsupportedFile, describe_options, load, read_table, sniff_options
from .schema import TARGETS, TEMPLATES, SchemaError, normalize_targets
from .suggest import suggest_targets

__all__ = ["TARGETS", "TEMPLATES", "MergePlan", "SchemaError", "SUPPORTED_SUFFIXES", "UnsupportedFile",
           "apply_plan", "build_plan", "column_previews", "describe_options", "generate_clean_script",
           "head_records", "load", "normalize_targets", "profile_file", "read_table", "same_script",
           "sniff_options", "suggest_targets"]
