#!/usr/bin/env python3
"""Test script to verify config_collector fix."""
import sys
sys.path.insert(0, 'scripts')

from pathlib import Path
from scripts.exposure.contracts import ExposureContext
from scripts.exposure.collectors.config_collector import ConfigCollector

ctx = ExposureContext(
    project_root=Path(r'D:\code\WebGoat-2025.3'),
    group_id='org.owasp.webgoat',
    loop_audit_dir=Path(r'D:\tmp\config_test'),
)

c = ConfigCollector()
result = c.collect(ctx)

print(f"Files found: {result.stats['total_files']}")
print(f"By type: {result.stats['by_type']}")
print(f"Secrets detected: {result.stats['secrets_detected']}")
print()

print("Sample items (first 5):")
for item in result.items[:5]:
    print(f"  {item['file']} ({item['file_type']}, {item['size_bytes']}b, secret={item['contains_secret']})")

print()
print("Verification:")
print(f"  ✓ No 'copied_path' field: {'copied_path' not in result.items[0] if result.items else 'N/A'}")
print(f"  ✓ No 'original_path' field: {'original_path' not in result.items[0] if result.items else 'N/A'}")
print(f"  ✓ Has 'file' field (relative path): {'file' in result.items[0] if result.items else 'N/A'}")
