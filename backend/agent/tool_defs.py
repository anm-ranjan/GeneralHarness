"""Tool JSON schema definitions for the MyHarness agent."""

READ_ONLY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "Search for files by name pattern in a directory. Returns matching file paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to search in."},
                    "pattern": {"type": "string", "description": "Filename pattern with wildcards."},
                    "recursive": {"type": "boolean", "description": "Whether to search subdirectories recursively. Default: true."},
                },
                "required": ["directory", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_list",
            "description": "List files under a directory. Uses ripgrep when available, with a Python fallback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to list."},
                    "glob": {"type": "string", "description": "Optional glob filter such as *.py or **/*.md."},
                    "max_results": {"type": "integer", "description": "Maximum results. Default: 100."},
                },
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "content_search",
            "description": "Search file contents with a regex. Uses ripgrep when available, with a Python fallback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to search."},
                    "query": {"type": "string", "description": "Regex or text to search for."},
                    "glob": {"type": "string", "description": "Optional file glob such as *.py or **/*.md."},
                    "case_sensitive": {"type": "boolean", "description": "Case-sensitive search. Default: false."},
                    "max_results": {"type": "integer", "description": "Maximum matching lines. Default: 100."},
                },
                "required": ["directory", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": (
                "Read a file. For large files, use read_mode='tail' to read the last N lines, "
                "'head' for the first N lines, or 'range' with offset to read a specific chunk. "
                "Use read_mode='search' with a query to find and return content around matches. "
                "For PDFs, 'lines' means pages (max 20 per call); 'search' returns matching pages. "
                "Plain text is supported; PDFs are supported if PyMuPDF is installed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Full path to read."},
                    "read_mode": {
                        "type": "string",
                        "enum": ["full", "head", "tail", "range", "search"],
                        "description": "How to read the file. 'full' (default) reads entire file (subject to size limit). 'head' reads first N lines. 'tail' reads last N lines. 'range' reads N lines starting from offset. 'search' finds matches for query and returns surrounding context.",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of lines to read (head/tail/range) or context lines around each match (search, default 5). For PDFs, this means pages.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Starting line number (0-based). Only used with 'range' mode.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search string. Required for read_mode='search'. For PDFs, searches extracted page text. For text files, searches line content.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image_read",
            "description": (
                "Load a local GIF, JPEG, PNG, or WebP image and attach it to the next model turn for visual "
                "inspection. Use this instead of file_read whenever the target is an image."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Full path of the local image to inspect."},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_request",
            "description": "Fetch content from a web URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch."},
                    "method": {"type": "string", "description": "HTTP method.", "enum": ["GET", "POST"]},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gather_context",
            "description": (
                "Run several independent read-only discovery jobs in parallel and return one "
                "compact evidence packet grouped by job. Use this instead of many sequential "
                "content_search/file_list/file_read calls when you need broad orientation: tracing a "
                "feature across backend, frontend, and tests, finding callers/imports, locating "
                "related tests, reading windows around several hits, or extracting structured "
                "inventories. Jobs never edit files. Each job needs a short 'name' and scoped "
                "'paths'. Do not use it for a single known file or a small targeted edit; for "
                "one known large file, prefer one full file_read so browser-backed providers can "
                "receive the whole file through their attachment path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "jobs": {
                        "type": "array",
                        "description": "List of independent read-only jobs (1 to budget.max_jobs).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["search", "test_discovery", "read_slices", "inventory"],
                                    "description": (
                                        "'search' returns bounded file:line matches for the patterns (paths are directories). "
                                        "'test_discovery' finds likely test files related to the patterns and reports candidate "
                                        "test commands without running them (paths are directories). "
                                        "'read_slices' reads narrow numbered windows around pattern matches or 'at_lines' in "
                                        "specific files (paths are files). "
                                        "'inventory' extracts structured items (paths are directories); patterns select kinds: "
                                        "routes, env, events, slash, config (default: all)."
                                    ),
                                },
                                "name": {"type": "string", "description": "Short label describing what this job gathers."},
                                "paths": {
                                    "type": "array",
                                    "description": "Inside the allowed paths. Directories for search/test_discovery/inventory; specific files for read_slices.",
                                    "items": {"type": "string"},
                                },
                                "patterns": {
                                    "type": "array",
                                    "description": "Literal or regex terms (search/read_slices), filter terms (test_discovery), or inventory kinds (inventory).",
                                    "items": {"type": "string"},
                                },
                                "at_lines": {
                                    "type": "array",
                                    "description": "read_slices only: explicit 1-based line numbers to read a window around.",
                                    "items": {"type": "integer"},
                                },
                                "context": {"type": "integer", "description": "read_slices only: lines of context around each anchor. Default 6."},
                                "glob": {"type": "string", "description": "Optional file glob filter such as *.py or *.jsx."},
                                "max_matches": {"type": "integer", "description": "Max matches/slices for this job. Default 24."},
                            },
                            "required": ["type", "name", "paths"],
                        },
                    },
                    "budget": {
                        "type": "object",
                        "description": "Optional overall limits for the batch.",
                        "properties": {
                            "timeout_seconds": {"type": "integer", "description": "Wall-clock limit for the whole batch. Default 12."},
                            "max_total_chars": {"type": "integer", "description": "Max characters across all job output. Default 20000."},
                            "max_jobs": {"type": "integer", "description": "Max jobs accepted. Default 8."},
                        },
                    },
                },
                "required": ["jobs"],
            },
        },
    },
]

DOMAIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lsdyna_keyword_lookup",
            "description": (
                "Look up an LS-DYNA keyword definition from the keyword manual knowledge base. "
                "Returns card layout (variable names, types, defaults, field widths), variable "
                "descriptions, and remarks. Always call this BEFORE writing or editing LS-DYNA "
                "keyword files to ensure correct card format. Large keyword definitions are "
                "returned with the full card layout intact but abbreviated descriptions; pass "
                "'variable' or 'card_index' to retrieve the full untruncated detail for one field "
                "or one card."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Keyword name, e.g. *SECTION_SHELL or *CONTROL_IMPLICIT_GENERAL.",
                    },
                    "variable": {
                        "type": "string",
                        "description": "Optional. Return full type/default/field-width and untruncated description for this one variable (e.g. ELFORM).",
                    },
                    "card_index": {
                        "type": "integer",
                        "description": "Optional. Return the full untruncated detail for just this 0-based card.",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsdyna_format_card",
            "description": (
                "Format LS-DYNA card data lines with correct field widths. "
                "Returns ready-to-use text including the $# comment header and data lines. "
                "Call lsdyna_keyword_lookup first to get the card layout, then call this "
                "to produce correctly spaced output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Keyword name, e.g. *DEFINE_CURVE.",
                    },
                    "card_index": {
                        "type": "integer",
                        "description": "0-based index of the card to format.",
                    },
                    "rows": {
                        "type": "array",
                        "description": (
                            "List of rows, each row is a list of values (strings or numbers). "
                            "For single-row cards, pass one row. For repeating cards (e.g. curve data), "
                            "pass multiple rows."
                        ),
                        "items": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "required": ["keyword", "card_index", "rows"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lasso_lookup",
            "description": (
                "Look up lasso-python post-processing reference for reading LS-Dyna "
                "binary output (d3plot, binout). Returns class APIs, array shapes and "
                "axis semantics, file routing, or agent workflow protocol. "
                "Always call this BEFORE writing code that opens d3plot/binout files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to look up. Examples: 'D3plot' (class API), "
                            "'Binout' (class API), 'element_shell_stress' (array shape), "
                            "'shell_stress' (partial match), 'routing' (file routing guide), "
                            "'protocol' (agent workflow), 'arrays' (all array shapes)."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
]

READ_ONLY_TOOLS = READ_ONLY_TOOLS + DOMAIN_TOOLS

WRITE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Create or overwrite a text file inside the allowed paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Full path to write."},
                    "content": {"type": "string", "description": "Complete file content."},
                    "overwrite": {"type": "boolean", "description": "Allow overwriting an existing file. Default: false."},
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_replace",
            "description": "Replace exact text in an existing file inside the allowed paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Full path to edit."},
                    "old_text": {"type": "string", "description": "Exact text to replace."},
                    "new_text": {"type": "string", "description": "Replacement text."},
                    "replace_all": {"type": "boolean", "description": "Replace all matches. Default: false."},
                },
                "required": ["file_path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply a simple patch envelope with Add File, Update File, and Delete File sections.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patch_text": {
                        "type": "string",
                        "description": "Patch text starting with *** Begin Patch and ending with *** End Patch.",
                    }
                },
                "required": ["patch_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_run",
            "description": "Run a shell command in an allowed working directory and return exit code, stdout, and stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to run."},
                    "working_directory": {"type": "string", "description": "Allowed directory where command should run."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds. Default: 120."},
                },
                "required": ["command", "working_directory"],
            },
        },
    },
]

TOOLS = READ_ONLY_TOOLS + WRITE_TOOLS
WRITE_TOOL_NAMES = frozenset(t["function"]["name"] for t in WRITE_TOOLS)
