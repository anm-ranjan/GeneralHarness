---
name: site-check
version: 1
description: Check whether web addresses respond, with status codes and response times
entry: run.py
dry_run: true
approval: auto_approve
tools: [run_agent_script]
timeout_seconds: 600
params:
  group: {type: string, label: Saved group, options_from: script, help: A group from sites.json; leave empty to check only the addresses below}
  urls: {type: strings, label: Addresses, pattern: '^https?://\S+$', help: One http(s) address per line}
  method: {type: enum, options: [HEAD, GET], default: HEAD, label: Request}
  timeout: {type: float, default: 10, min: 1, max: 60, label: Timeout (s)}
---
Read-only. Turn the request into addresses or a saved group for
run_agent_script. Report each address's status code or error exactly as the
script observed it. Never guess why a site is down.
