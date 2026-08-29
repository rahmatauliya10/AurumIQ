#!/usr/bin/env python
"""Standalone script wrapper to backfill candles."""
import os
import sys
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from django.core.management import call_command

if __name__ == "__main__":
    call_command("backfill_candles", *sys.argv[1:])
