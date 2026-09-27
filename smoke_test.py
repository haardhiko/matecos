# Quick import test
from src.api.routes.github_tools import _registry, _executor
print(f'Registry has {_registry.tool_count} tools')
for r in _registry.list_all():
    print(f'  - {r.tool_id}: {r.manifest.name}')
