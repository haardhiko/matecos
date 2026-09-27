import re

file_path = "C:/Users/haard/.gemini/antigravity/scratch/matecos/src/api/routes/github_tools.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

imports = """
from src.tools.builtins.json_format import handler as json_format_handler, JSON_FORMAT_MANIFEST
from src.tools.builtins.base64_tool import handler as base64_tool_handler, BASE64_TOOL_MANIFEST
from src.tools.builtins.hash_tool import handler as hash_tool_handler, HASH_TOOL_MANIFEST
from src.tools.builtins.uuid_tool import handler as uuid_tool_handler, UUID_TOOL_MANIFEST
from src.tools.builtins.regex_tool import handler as regex_tool_handler, REGEX_TOOL_MANIFEST
from src.tools.builtins.timestamp_tool import handler as timestamp_tool_handler, TIMESTAMP_TOOL_MANIFEST
from src.tools.builtins.word_count import handler as word_count_handler, WORD_COUNT_MANIFEST
from src.tools.builtins.url_parse import handler as url_parse_handler, URL_PARSE_MANIFEST
from src.tools.builtins.password_gen import handler as password_gen_handler, PASSWORD_GEN_MANIFEST
from src.tools.builtins.text_diff import handler as text_diff_handler, TEXT_DIFF_MANIFEST
from src.tools.builtins.encode_tool import handler as encode_tool_handler, ENCODE_TOOL_MANIFEST
from src.tools.builtins.unit_convert import handler as unit_convert_handler, UNIT_CONVERT_MANIFEST
"""

registers = """
_registry.register(JSON_FORMAT_MANIFEST)
_registry.register(BASE64_TOOL_MANIFEST)
_registry.register(HASH_TOOL_MANIFEST)
_registry.register(UUID_TOOL_MANIFEST)
_registry.register(REGEX_TOOL_MANIFEST)
_registry.register(TIMESTAMP_TOOL_MANIFEST)
_registry.register(WORD_COUNT_MANIFEST)
_registry.register(URL_PARSE_MANIFEST)
_registry.register(PASSWORD_GEN_MANIFEST)
_registry.register(TEXT_DIFF_MANIFEST)
_registry.register(ENCODE_TOOL_MANIFEST)
_registry.register(UNIT_CONVERT_MANIFEST)
"""

executors = """
_executor.register_builtin("text.json.format", json_format_handler)
_executor.register_builtin("text.base64.codec", base64_tool_handler)
_executor.register_builtin("crypto.hash.gen", hash_tool_handler)
_executor.register_builtin("util.uuid.gen", uuid_tool_handler)
_executor.register_builtin("text.regex.test", regex_tool_handler)
_executor.register_builtin("util.timestamp.convert", timestamp_tool_handler)
_executor.register_builtin("text.word.count", word_count_handler)
_executor.register_builtin("text.url.parse", url_parse_handler)
_executor.register_builtin("util.password.gen", password_gen_handler)
_executor.register_builtin("text.diff.compare", text_diff_handler)
_executor.register_builtin("text.encode.convert", encode_tool_handler)
_executor.register_builtin("data.convert.units", unit_convert_handler)
"""

if "JSON_FORMAT_MANIFEST" not in content:
    idx1 = content.find("_registry.register(csv_manifest())")
    if idx1 != -1:
        idx1 += len("_registry.register(csv_manifest())")
        content = content[:idx1] + "\n" + registers + content[idx1:]
        
    idx2 = content.find("_executor.register_builtin(\"data.csv.profile\", csv_profile_handler)")
    if idx2 != -1:
        idx2 += len("_executor.register_builtin(\"data.csv.profile\", csv_profile_handler)")
        content = content[:idx2] + "\n" + executors + content[idx2:]
        
    idx3 = content.find("from src.tools.builtins.csv_profile")
    if idx3 != -1:
        end_idx = content.find(")", idx3) + 1
        content = content[:end_idx] + "\n" + imports + content[end_idx:]

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
