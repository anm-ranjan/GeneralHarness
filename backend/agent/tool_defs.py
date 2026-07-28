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
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": "List the reusable skills installed in the Harness skills collection.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_read",
            "description": (
                "Read the complete SKILL.md instructions for one installed Harness skill. "
                "Call skill_list first when you do not know the exact skill name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill directory name, as returned by skill_list.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_update",
            "description": (
                "Publish or update a short plan/checklist of steps for the current task, shown to the user "
                "in the session view. Send the full current plan each time (it replaces whatever was shown "
                "before) - lay it out when starting a multi-step task, then call it again whenever a step's "
                "status changes. Skip it for simple one-shot requests that don't need a visible plan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "The full current plan, in order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "Short label for this step."},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Default: pending.",
                                },
                            },
                            "required": ["content"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
]

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
            "description": (
                "Run a shell command in an allowed working directory and return exit code, stdout, and stderr. "
                "This blocks until the command exits or the timeout is hit (max 600s), so it cannot host anything "
                "that stays running on its own — a dev server, a watch task, `tail -f`. For those, set "
                "background=true instead: the call returns immediately with a job id, and you poll it with "
                "shell_check and stop it with shell_kill."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to run."},
                    "working_directory": {"type": "string", "description": "Allowed directory where command should run."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds for a foreground run. Default: 120. Ignored when background=true."},
                    "background": {
                        "type": "boolean",
                        "description": (
                            "Start the command detached and return immediately with a job id instead of "
                            "waiting for it to exit. Use for dev servers, watch tasks, or anything long-running. "
                            "Default: false."
                        ),
                    },
                },
                "required": ["command", "working_directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_check",
            "description": "Poll a background job started by shell_run(background=true): whether it's still running (or its exit code), plus the tail of its stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job id returned by shell_run(background=true)."},
                    "tail_lines": {"type": "integer", "description": "Number of trailing lines to return per stream. Default: 200."},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_kill",
            "description": "Stop a background job started by shell_run(background=true).",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job id returned by shell_run(background=true)."},
                },
                "required": ["job_id"],
            },
        },
    },
]

TOOLS = READ_ONLY_TOOLS + WRITE_TOOLS
WRITE_TOOL_NAMES = frozenset(t["function"]["name"] for t in WRITE_TOOLS)
