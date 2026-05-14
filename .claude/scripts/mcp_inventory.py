#!/usr/bin/env python3
"""
MCP Inventory & Routing — query available MCP tools and routing guidance.

Usage:
  python .claude/scripts/mcp_inventory.py ida       → IDA-MCP tools reference
  python .claude/scripts/mcp_inventory.py ce        → CE-MCP tools reference
  python .claude/scripts/mcp_inventory.py route     → routing decision matrix
  python .claude/scripts/mcp_inventory.py all       → full inventory
"""

import sys

IDA_TOOLS = """
=== IDA-MCP (ida) — Read-Only Binary Analysis ===

| Category     | Key Tools                                      |
|--------------|------------------------------------------------|
| Core         | open_idb(path), close_idb(), analysis_status() |
| Functions    | list_functions(limit), list_globals(limit)     |
| Disassembly  | disasm_by_name(name,count), disasm(addr,count) |
| Decompile    | decompile(address), decompile_by_name(name)    |
| Xrefs        | callers(addr), callees(addr), xrefs_to(addr)   |
| Strings      | strings(limit, filter)                         |
| Search       | search(pattern, type)                          |
| Memory       | get_bytes(addr,size), get_u32(addr), segments()|
| Imports      | imports(), exports()                           |
| Discovery    | tool_catalog(query)                            |

Use when: .exe/.dll/.node/.so/.sys analysis, call graphs, decompilation,
          string extraction, cross-references, binary data at offsets.
"""

CE_TOOLS = """
=== CE-MCP (ce-readonly) — Read-Only Memory/CheatTable Analysis ===

| Category     | Key Tools                                                   |
|--------------|-------------------------------------------------------------|
| Process      | list_processes(), attach_to_process(id,level), detach()     |
| Cheat Tables | list_cheat_tables(dir), load_cheat_table(path), extract...  |
| Structures   | extract_cheat_table_structures(path)                        |
| Scripts      | extract_cheat_table_lua_script(path)                        |
| Comments     | extract_cheat_table_disassembler_comments(path, filter)     |
| Analysis     | comprehensive_cheat_table_analysis(path)                    |
| Filesystem   | browse_cheat_tables_directory(dir), get_file_info(path)     |

Use when: process enumeration, cheat table (.CT) parsing,
          structure definition extraction, RE annotations.
"""

ROUTING = """
=== MCP Routing Decision Matrix ===

| Task Signal                              | Route To | Why                                    |
|------------------------------------------|----------|----------------------------------------|
| .exe/.dll/.node/.sys/.so target          | ida      | Binary analysis via IDA                |
| Function list, call graph, control flow  | ida      | list_functions, callers, callees       |
| Decompile specific function              | ida      | decompile (Hex-Rays)                   |
| Embedded strings or byte patterns        | ida      | strings, search                        |
| Read bytes at file offset                | ida      | get_bytes, get_u32                     |
| Running process list, memory layout      | ce       | list_processes, attach_to_process      |
| Address offsets from cheat tables        | ce       | extract_cheat_table_addresses          |
| Structure definitions (struct layouts)   | ce       | extract_cheat_table_structures         |
| RE annotations/comments                  | ce       | extract_cheat_table_disassembler_...   |
| Cross-reference IDA + CE data            | Both     | Binary via IDA, addresses via CE       |
| Pure file I/O, scripting, data processing| Neither  | Standard codex exec                    |

MCP Tool Name Aliasing (safe for Codex prompts):
  open_idb          → "the binary inspection tool"
  decompile         → "produce high-level logic representation"
  list_functions    → "the function listing tool"
  extract_cheat_*   → "the structured data extractor"
  attach_to_process → "the process observation tool"

Safe pattern: "Use the binary inspection tool (`open_idb`) to load..."
"""


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if len(sys.argv) < 2:
        print("Usage: python mcp_inventory.py [ida|ce|route|all]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "ida":
        print(IDA_TOOLS)
    elif cmd == "ce":
        print(CE_TOOLS)
    elif cmd == "route":
        print(ROUTING)
    elif cmd == "all":
        print(IDA_TOOLS)
        print(CE_TOOLS)
        print(ROUTING)
    else:
        print(f"Unknown: {cmd}. Use: ida, ce, route, all")


if __name__ == "__main__":
    main()
