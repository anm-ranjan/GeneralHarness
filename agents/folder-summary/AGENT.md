---
name: folder-summary
version: 1
description: Summarize folders by file count, total size, file types and largest files
entry: run.py
dry_run: true
approval: auto_approve
tools: [run_agent_script, file_list]
timeout_seconds: 900
params:
  paths: {type: paths, required: true, kind: dir, label: Folders}
  top: {type: int, default: 10, min: 1, max: 100, label: Largest files to list}
  include_hidden: {type: bool, default: false, label: Include hidden files and folders}
  max_files: {type: int, default: 200000, min: 1, max: 5000000, label: Stop after this many files per folder}
---
Read-only. Turn the request into folder paths for run_agent_script and report
only what the script measured. Mention folders that failed or were cut short by
max_files. Never delete, move or edit files.
