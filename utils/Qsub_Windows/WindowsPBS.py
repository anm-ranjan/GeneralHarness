#!/usr/bin/env python3
"""
PBS PRO Job Monitor and Submitter
Windows CLI tool to monitor and submit jobs across multiple Linux servers
"""

import paramiko
import json
import sys
import os
import time
import shutil
import subprocess
import threading
from datetime import datetime
import yaml
from collections import OrderedDict
from getpass import getuser
from pathlib import Path


def load_config(config_path=None):
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to config file. If None, searches in:
                     1. ./config.yaml
                     2. ~/.pbs_monitor/config.yaml
                     3. Script directory/config.yaml
    
    Returns:
        dict: Configuration dictionary
    """
    search_paths = []
    
    if config_path:
        search_paths.append(config_path)
    else:
        # Current directory
        search_paths.append(Path("./server_config.yaml"))
        # User's home directory
        search_paths.append(Path.home() / ".pbs_monitor" / "server_config.yaml")
        # Script directory
        search_paths.append(Path(__file__).parent / "server_config.yaml")
    
    for path in search_paths:
        if Path(path).exists():
            try:
                with open(path, 'r') as f:
                    config = yaml.safe_load(f)
                print(f"✓ Loaded configuration from: {path}")
                return config
            except yaml.YAMLError as e:
                print(f"❌ Error parsing config file {path}: {e}")
                sys.exit(1)
    
    print("❌ Configuration file not found!")
    print("   Searched in:")
    for path in search_paths:
        print(f"   - {path}")
    print("\n   Please create a server_config.yaml file.")
    sys.exit(1)


def validate_config(config):
    """Validate that all required configuration keys are present."""
    required_keys = {
        'pbs': ['qdel_path', 'qsub_path'],
        'paths': ['linux_base_path', 'remote_script_name'],
        'drive_mapping': None,  # Just needs to exist
        'servers': None  # Just needs to exist and be a list
    }
    
    errors = []
    
    for section, keys in required_keys.items():
        if section not in config:
            errors.append(f"Missing section: '{section}'")
            continue
        
        if keys:
            for key in keys:
                if key not in config[section]:
                    errors.append(f"Missing key: '{section}.{key}'")
    
    # Validate servers is a list with required fields
    if 'servers' in config:
        if not isinstance(config['servers'], list):
            errors.append("'servers' must be a list")
        elif len(config['servers']) == 0:
            errors.append("'servers' list cannot be empty")
        else:
            for i, server in enumerate(config['servers']):
                if 'hostname' not in server:
                    errors.append(f"Server {i+1}: missing 'hostname'")
                if 'name' not in server:
                    errors.append(f"Server {i+1}: missing 'name'")
    
    # Validate drive_mapping is a dict
    if 'drive_mapping' in config:
        if not isinstance(config['drive_mapping'], dict):
            errors.append("'drive_mapping' must be a dictionary")
        elif len(config['drive_mapping']) == 0:
            errors.append("'drive_mapping' cannot be empty")
    
    if errors:
        print("❌ Configuration validation errors:")
        for error in errors:
            print(f"   - {error}")
        sys.exit(1)
    
    return True


class PBSJobManager:
    def __init__(self, config, user):
        """
        Initialize with configuration and username.
        
        Args:
            config: Configuration dictionary from YAML
            user: Current username for SSH connections
        """
        self.config = config
        self.user = user
        self.all_jobs = []
        
        # Extract configuration values
        self.qdel_path = config['pbs']['qdel_path']
        self.qsub_path = config['pbs']['qsub_path']
        self.linux_base_path = config['paths']['linux_base_path']
        self.remote_script_name = config['paths']['remote_script_name']
        self.drive_mapping = config['drive_mapping']
        self.ssh_timeout = config.get('ssh', {}).get('connection_timeout', 10)
        
        # Create reverse mapping: server hostname -> drive letter
        self.server_to_drive = {hostname: drive for drive, hostname in self.drive_mapping.items()}
        
        # Setup servers with username and key_file
        self.servers = self._setup_servers(config['servers'])
        
        # Calculate script paths
        self.script_dir = f"{self.linux_base_path}/{self.user}"
        self.script_path = f"{self.script_dir}/{self.remote_script_name}"
    
    def _setup_servers(self, servers_config):
        """Add username and key_file to server configurations."""
        # Get user's home directory for SSH key
        user_home = os.path.expanduser("~")
        default_key_path = os.path.join(user_home, ".ssh", "id_rsa")
        
        # Check for config override or default key
        config_key = self.config.get('ssh', {}).get('key_file', '')
        
        if config_key and os.path.exists(config_key):
            use_key = config_key
            print(f"🔑 Using SSH key from config: {use_key}")
        elif os.path.exists(default_key_path):
            use_key = default_key_path
            print(f"🔑 Found SSH key: {default_key_path}")
        else:
            use_key = None
            print(f"⚠️  No SSH key found. You may be prompted for passwords.")
        
        servers = []
        for srv in servers_config:
            server = {
                'hostname': srv['hostname'],
                'name': srv['name'],
                'username': self.user,
                'key_file': use_key
            }
            servers.append(server)
        
        return servers
    
    def connect_and_execute(self, server, command):
        """Execute command on remote server via SSH"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect using SSH key or password
            if 'key_file' in server and server['key_file']:
                ssh.connect(
                    server['hostname'],
                    username=server['username'],
                    key_filename=server['key_file'],
                    timeout=self.ssh_timeout
                )
            else:
                ssh.connect(
                    server['hostname'],
                    username=server['username'],
                    timeout=self.ssh_timeout
                )
            
            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')
            
            ssh.close()
            
            if error and not output:
                print(f"Error from {server['hostname']}: {error}")
                return None
            
            return output
            
        except Exception as e:
            print(f"Connection error to {server['hostname']}: {str(e)}")
            return None
    
    def parse_output(self, output, server_name):
        """Parse the JSON output from the Python script"""
        try:
            jobs_data = json.loads(output)
            jobs = []
            
            for job_data in jobs_data:
                job = OrderedDict([
                    ('Server', server_name),
                    ('JobID', job_data.get('JobID', 'N/A')),
                    ('Job_Name', job_data.get('Job_Name', 'N/A')),
                    ('Job_Path', job_data.get('Job_Path', 'N/A')),
                    ('CPUs', str(job_data.get('CPUs', 'N/A'))),
                    ('Status', job_data.get('Status', 'N/A')),
                    ('Owner', job_data.get('Owner', 'N/A')),
                    ('Memory', job_data.get('Memory', 'N/A'))
                ])
                jobs.append(job)
            
            return jobs
            
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON from {server_name}: {str(e)}")
            return []
    
    def fetch_all_jobs(self):
        """Fetch jobs from all servers"""
        self.all_jobs = []
        
        print("\n🔄 Fetching jobs from all servers...\n")
        
        for server in self.servers:
            print(f"📡 Connecting to {server['name']}...", end=' ')
            
            command = f"python3 {self.script_path} --json"
            output = self.connect_and_execute(server, command)
            
            if output:
                jobs = self.parse_output(output, server['name'])
                self.all_jobs.extend(jobs)
                print(f"✓ Found {len(jobs)} jobs")
            else:
                print("✗ Failed")
        
        print(f"\n📊 Total jobs found: {len(self.all_jobs)}\n")
        return self.all_jobs
    
    def display_jobs(self, jobs=None, sort_by='JobID'):
        """Display jobs in a formatted table"""
        if jobs is None:
            jobs = self.all_jobs
        
        if not jobs:
            print("❌ No jobs to display\n")
            return
        
        # Sort jobs
        if sort_by in ['CPUs']:
            jobs = sorted(jobs, key=lambda x: int(x[sort_by]) if x[sort_by].isdigit() else 0)
        else:
            jobs = sorted(jobs, key=lambda x: x[sort_by])
        
        # Define column widths
        widths = {
            'Server': 20,
            'JobID': 35,
            'Job_Name': 30,
            'Job_Path': 100,
            'CPUs': 6,
            'Status': 8,
            'Owner': 10,
            'Memory': 10
        }
        
        # Print header
        header = ""
        separator = ""
        for field, width in widths.items():
            header += f"{field:<{width}} "
            separator += "-" * width + " "
        
        print(header)
        print(separator)
        
        # Print jobs
        for job in jobs:
            row = ""
            for field, width in widths.items():
                value = job.get(field, 'N/A')
                if len(value) > width - 1:
                    value = value[:width-4] + "..."
                row += f"{value:<{width}} "
            print(row)
        
        print()
    
    def kill_job(self, job_id, server_hostname=None):
        """Kill a job on specified server"""
        # If server not specified, try to find it from job list
        job_path = ""
        if not server_hostname:
            for job in self.all_jobs:
                if job_id in job['JobID']:
                    server_hostname = job['Server']
                    job_path = job['Job_Path']
                    break
        
        if not server_hostname:
            print(f"❌ Could not determine server for job {job_id}")
            return False
        
        # Find the server config
        server = None
        for s in self.servers:
            if s['name'] == server_hostname:
                server = s
                break
        
        if not server:
            print(f"❌ Server {server_hostname} not found in configuration")
            return False
        
        print(f"\n🗑️  Killing job {job_id} on {server_hostname}...")
        
        command = f"{self.qdel_path} {job_id}"
        output = self.connect_and_execute(server, command)
        
        if output is not None:
            print(f"✓ Job {job_id} killed successfully!")
            if output.strip():
                print(f"  Output: {output.strip()}")
            del_dir = input("\nDelete job directory (y/n): ").strip()
            if del_dir == 'y':
                job_path = job_path + "/Simulation"
                command = f"rm -rf {job_path}"
                output = self.connect_and_execute(server, command)
                print(f"✓ Deleted source directory: {job_path}\n")
            elif del_dir == 'n':
                print(f"✓ Retained source directory: {job_path}\n")
            else:
                print(f"❌ Invalid choice\n")
                
            return True
        else:
            print(f"✗ Failed to kill job {job_id}\n")
            return False
    
    def view_log(self, job_id_input):
        """View log file for a job (like tail -f)"""
        # Find the job in our list
        matched_job = None
        
        for job in self.all_jobs:
            if job_id_input in job['JobID'] or job['JobID'].startswith(job_id_input + '.'):
                matched_job = job
                break
        
        if not matched_job:
            print(f"\n❌ Job ID '{job_id_input}' not found in current job list")
            print(f"   Please refresh the job list first (option 8)")
            return False
        
        server_hostname = matched_job['Server']
        job_path = matched_job['Job_Path']
        log_file = f"{job_path}/Simulation/messag"
        
        # Find the server config
        server = None
        for s in self.servers:
            if s['name'] == server_hostname:
                server = s
                break
        
        if not server:
            print(f"❌ Server {server_hostname} not found in configuration")
            return False
        
        # Check if log file exists
        check_cmd = f"test -f {log_file} && echo 'EXISTS' || echo 'NOT_FOUND'"
        result = self.connect_and_execute(server, check_cmd)
        
        if not result or 'NOT_FOUND' in result:
            print(f"\n❌ Log file not found: {log_file}")
            return False
        
        print(f"\n📄 Viewing log for job: {matched_job['JobID']}")
        print(f"   Job Name: {matched_job['Job_Name']}")
        print(f"   Server: {server_hostname}")
        print(f"   Log file: {log_file}")
        print(f"\n{'=' * 70}")
        print("Press Ctrl+C to stop watching the log")
        print('=' * 70 + '\n')
        
        # Get initial content (last 50 lines)
        initial_cmd = f"tail -n 50 {log_file}"
        initial_output = self.connect_and_execute(server, initial_cmd)
        
        if initial_output:
            print(initial_output)
        
        # Keep track of file size for detecting new content
        prev_size = 0
        size_cmd = f"stat -c %s {log_file} 2>/dev/null || stat -f %z {log_file}"
        size_result = self.connect_and_execute(server, size_cmd)
        if size_result and size_result.strip().isdigit():
            prev_size = int(size_result.strip())
        
        try:
            while True:
                time.sleep(3)
                
                # Check current file size
                size_result = self.connect_and_execute(server, size_cmd)
                if not size_result or not size_result.strip().isdigit():
                    continue
                
                current_size = int(size_result.strip())
                
                # If file grew, get new content
                if current_size > prev_size:
                    # Get new bytes
                    bytes_to_read = current_size - prev_size
                    new_content_cmd = f"tail -c {bytes_to_read} {log_file}"
                    new_content = self.connect_and_execute(server, new_content_cmd)
                    
                    if new_content:
                        print(new_content, end='', flush=True)
                    
                    prev_size = current_size
                elif current_size < prev_size:
                    # File was truncated or replaced
                    print(f"\n[Log file was reset/truncated]\n")
                    new_content_cmd = f"tail -n 50 {log_file}"
                    new_content = self.connect_and_execute(server, new_content_cmd)
                    if new_content:
                        print(new_content)
                    prev_size = current_size
                
        except KeyboardInterrupt:
            print(f"\n\n{'=' * 70}")
            print("✓ Stopped watching log file")
            print('=' * 70 + '\n')
            return True
            
    def generate_report_interactive(self):
        """
        Interactive report generation with optional viewer launch.
        
        Workflow:
        1. Get Windows path (WINDIR) from user
        2. Convert to Linux path (LINDIR)
        3. Execute run_report.sh on the server
        4. Optionally launch the HTML report viewer

        """
        
        print("\n" + "=" * 70)
        print("                     Generate Report")
        print("=" * 70)
        
        # Get Windows path from user
        win_path = input("\nEnter full Windows path (WINDIR): ").strip()
        
        if not win_path:
            print("❌ No path provided")
            return False
        
        # Normalize the path
        win_path = os.path.abspath(win_path)
        
        # Guardrail: Check if Windows path exists
        if not os.path.exists(win_path):
            print(f"❌ Windows path does not exist: {win_path}")
            return False
        
        # Convert to Linux path and get server
        server_hostname, linux_path = self.windows_to_linux_path(win_path)
        
        if not server_hostname:
            drive_letters = ', '.join(self.drive_mapping.keys())
            print(f"❌ Path is not on a mapped drive ({drive_letters})")
            return False
        
        print(f"\n✓ Windows path (WINDIR): {win_path}")
        print(f"✓ Linux path (LINDIR):   {linux_path}")
        print(f"✓ Target server:         {server_hostname}")
        
        # Find the server config
        server = None
        for s in self.servers:
            if s['hostname'] == server_hostname:
                server = s
                break
        
        if not server:
            print(f"❌ Server {server_hostname} not found in configuration")
            return False
                
        # =========================================================================
        # Guardrail 1: Check if run_report_win.sh exists on the server PATH
        # =========================================================================
        print(f"\n🔍 Checking if 'run_report_win.sh' is available on server...")
        check_cmd = 'bash -i -c "which run_report_win.sh 2>/dev/null || command -v run_report_win.sh 2>/dev/null"'
        result = self.connect_and_execute(server, check_cmd)
        
        if not result or not result.strip():
            print("❌ 'run_report_win.sh' not found in server's PATH")
            print("   Please ensure the script is installed and accessible via $PATH on the server.")
            return False
        
        report_script_path = result.strip()
        print(f"✓ Found run_report_win.sh at: {report_script_path}")
        
        # =========================================================================
        # Execute run_report_win.sh with LINDIR as argument
        # =========================================================================
        print(f"\n🚀 Executing: run_report_win.sh {linux_path}")
        print("-" * 70)
        
        # Quote the path to handle spaces
        report_cmd = f'bash -i -c \'run_report_win.sh "{linux_path}"\''
        output = self.connect_and_execute(server, report_cmd)
        
        if output is None:
            print("❌ Report generation failed - no response from server")
            return False
        
        # Display output from report generation
        if output.strip():
            print(output.strip())
        
        print("-" * 70)
        print("✓ Report generation command completed!")
        time.sleep(0.5)
        
        # =========================================================================
        # Ask if user wants to view the report
        # =========================================================================
        print("\n" + "=" * 70)
        view_choice = input("Do you want to view the report? (y/n): ").strip().lower()
        
        if view_choice not in ['y', 'yes']:
            print("✓ Report generation complete. Skipping viewer.")
            return True
        
        # Define paths for the HTML viewer
        html_dir = os.path.join(win_path, "Simulation", "_HTML")
        server_cmd_path = os.path.join(html_dir, "start_server.cmd")
        
        # =========================================================================
        # Guardrail 2: Check if HTML directory exists
        # =========================================================================
        if not os.path.isdir(html_dir):
            print(f"\n❌ HTML directory not found: {html_dir}")
            print("   The report may not have been generated correctly.")
            print("   Expected directory structure: WINDIR/Simulation/_HTML/")
            return False
        
        print(f"\n✓ Found HTML directory: {html_dir}")
        
        # =========================================================================
        # Guardrail 3: Check if start_server.cmd exists
        # =========================================================================
        if not os.path.isfile(server_cmd_path):
            print(f"❌ Viewer script not found: {server_cmd_path}")
            print("   Expected 'start_server.cmd' in the _HTML directory.")
            return False
        
        print(f"✓ Found viewer script: {server_cmd_path}")
        
        # =========================================================================
        # Launch the server in batch mode
        # =========================================================================
        print(f"\n🌐 Starting report server...")
        
        try:
            # Change to HTML directory and execute start_server.cmd
            # Using 'start' command to open in a new window
            # /D sets the working directory
            # 'call' ensures batch file execution
            launch_cmd = f'start "Report Server" /D "{html_dir}" cmd /c "call start_server.cmd"'
            
            subprocess.Popen(
                launch_cmd,
                shell=True,
                cwd=html_dir
            )
            
            print("✓ Report server launched in new window")
            print("\n📝 Note: Close the server window when done viewing the report.")
            
        except FileNotFoundError as e:
            print(f"❌ Failed to start report server: Command not found")
            print(f"   Error: {str(e)}")
            return False
        except PermissionError as e:
            print(f"❌ Failed to start report server: Permission denied")
            print(f"   Error: {str(e)}")
            return False
        except Exception as e:
            print(f"❌ Failed to start report server: {str(e)}")
            return False
        
        return True
    
    def windows_to_linux_path(self, windows_path):
        """Convert Windows path to Linux path and determine server"""
        windows_path = os.path.abspath(windows_path)
        drive = windows_path[0].upper()
        
        if drive not in self.drive_mapping:
            return None, None
        
        server_hostname = self.drive_mapping[drive]
        
        # Remove drive letter and convert to Linux path
        path_after_drive = windows_path[2:].replace('\\', '/')
        linux_path = f"{self.linux_base_path}/{self.user}{path_after_drive}"
        
        return server_hostname, linux_path
    
    def get_drive_letter(self, path):
        """Extract drive letter from path"""
        abs_path = os.path.abspath(path)
        return abs_path[0].upper() if len(abs_path) > 0 else None
    
    def copy_directory_contents(self, source, destination):
        """Copy all contents from source to destination"""
        try:
            if not os.path.exists(destination):
                os.makedirs(destination)
                print(f"✓ Created directory: {destination}")
            
            print(f"📁 Copying files from {source} to {destination}...")
            
            for item in os.listdir(source):
                source_item = os.path.join(source, item)
                dest_item = os.path.join(destination, item)
                
                if os.path.isdir(source_item):
                    shutil.copytree(source_item, dest_item)
                else:
                    shutil.copy2(source_item, dest_item)
            
            print(f"✓ Successfully copied all files\n")
            return True
            
        except Exception as e:
            print(f"❌ Error copying files: {str(e)}\n")
            return False
    
    def submit_job_interactive(self):
        """Interactive job submission with path handling"""
        print("\n" + "=" * 70)
        print("                     Submit New Job")
        print("=" * 70)
        
        # Get current directory
        current_dir = os.getcwd()
        print(f"\n📂 Current directory: {current_dir}")
        
        # Ask user choice
        print("\nWhere do you want to run the job from?")
        print("[1] Current directory")
        print("[2] Custom path")
        
        choice = input("\nEnter choice: ").strip()
        
        server_hostname = None
        job_path = None
        
        if choice == '1':
            # Option i) - Run from current directory
            server_hostname, job_path = self.windows_to_linux_path(current_dir)
            
            if not server_hostname:
                drive_letters = ', '.join(self.drive_mapping.keys())
                print(f"❌ Current directory is not on a mapped drive ({drive_letters})")
                return False
            
            print(f"\n✓ Server: {server_hostname}")
            print(f"✓ Linux path: {job_path}\n")
            
        elif choice == '2':
            # Option ii) - Custom path
            custom_path = input("\nEnter custom path: ").strip()
            
            if not os.path.exists(custom_path):
                print(f"❌ Path does not exist: {custom_path}")
                return False
            
            drive = self.get_drive_letter(custom_path)
            
            if drive in self.drive_mapping:
                # Custom path is on mapped drive
                server_hostname, job_path = self.windows_to_linux_path(custom_path)
                print(f"\n✓ Server: {server_hostname}")
                print(f"✓ Linux path: {job_path}\n")
                
            else:
                # Custom path (HOME) is NOT on mapped drive
                drive_letters = ', '.join(self.drive_mapping.keys())
                print(f"\n⚠️  Path is not on a mapped drive ({drive_letters})")
                print(f"   You need to copy files to a mapped location.\n")
                
                # Ask for server
                print("Available servers:")
                for i, srv in enumerate(self.servers, 1):
                    drive_letter = self.server_to_drive.get(srv['hostname'], '?')
                    print(f"  [{i}] {srv['name']} (Drive {drive_letter}:)")
                
                srv_choice = input("\nSelect server (number): ").strip()
                try:
                    srv_idx = int(srv_choice) - 1
                    if 0 <= srv_idx < len(self.servers):
                        server_hostname = self.servers[srv_idx]['hostname']
                    else:
                        print("❌ Invalid server selection")
                        return False
                except ValueError:
                    print("❌ Invalid input")
                    return False
                
                # Get the corresponding drive letter for the selected server
                required_drive = self.server_to_drive.get(server_hostname)
                if not required_drive:
                    print(f"❌ No drive mapping found for server {server_hostname}")
                    return False
                
                print(f"\n✓ Selected server: {server_hostname}")
                print(f"✓ Destination must be on drive {required_drive}:\n")
                
                while True:
                    dest_path = input(f"Enter destination path (must start with {required_drive}:\\): ").strip()
                    dest_drive = self.get_drive_letter(dest_path)
                    
                    if dest_drive != required_drive:
                        print(f"❌ Destination must be on drive {required_drive}: (selected server: {server_hostname})")
                        continue
                    
                    # Check if destination exists
                    if os.path.exists(dest_path):
                        if os.listdir(dest_path):  # Directory is not empty
                            print(f"\n⚠️  WARNING: Destination directory is not empty!")
                            print(f"   Files in: {dest_path}")
                            action = input("   [1] Enter new path  [2] Continue anyway: ").strip()
                            
                            if action == '1':
                                continue
                            elif action != '2':
                                print("❌ Invalid choice")
                                return False
                        else:
                            print(f"✓ Destination directory exists and is empty")
                    
                    # Copy files
                    if not self.copy_directory_contents(custom_path, dest_path):
                        return False
                    
                    # Extract server and job path from DEST
                    _, job_path = self.windows_to_linux_path(dest_path)
                    print(f"✓ Server: {server_hostname}")
                    print(f"✓ Linux path: {job_path}\n")
                    break
        
        else:
            print("❌ Invalid choice")
            return False
        
        # Now submit the job
        script_name = "qsubrunfhgfs.sh"
        time.sleep(1)
        
        return self.submit_job(server_hostname, job_path, script_name)
    
    def submit_job(self, server_hostname, job_path, script_name="qsubrunfhgfs.sh"):
        """Submit a job to specified server"""
        # Find the server config
        server = None
        for s in self.servers:
            if s['hostname'] == server_hostname:
                server = s
                break
        
        if not server:
            print(f"❌ Server {server_hostname} not found in configuration")
            return False
        
        print(f"\n🚀 Submitting job on {server_hostname}...")
        print(f"   Path: {job_path}")
        print(f"   Script: {script_name}\n")
        
        command = f"cd {job_path} && {self.qsub_path} {script_name}"
        output = self.connect_and_execute(server, command)
        
        if output:
            print(f"✓ Job submitted successfully!")
            print(f"  Output: {output.strip()}\n")
            
            input("Press Enter to refresh job list...")
            self.fetch_all_jobs()
            self.display_jobs()
            
            return True
        else:
            print(f"✗ Job submission failed\n")
            return False
            
    def document_job_description(self):
        """
        Document job context/description for a simulation folder.
        
        Allows user to provide context either:
        - Manually via multi-line text input
        - Via audio transcription (60s max, early stop with Enter)
        
        Context is saved to WINDIR/Context.txt with timestamp.
        If file exists, new content is appended with timestamp separator.
        """
        
        print("\n" + "=" * 70)
        print("               Document Job Description")
        print("=" * 70)
        
        # =========================================================================
        # Step a) Get Windows path from user
        # =========================================================================
        win_path = input("\nEnter full Windows path (WINDIR): ").strip()
        
        if not win_path:
            print("❌ No path provided")
            return False
        
        # Normalize the path
        win_path = os.path.abspath(win_path)
        
        # Guardrail: Check if Windows path exists
        if not os.path.exists(win_path):
            print(f"❌ Windows path does not exist: {win_path}")
            return False
        
        print(f"\n✓ Target directory: {win_path}")
        
        # Check if Context.txt already exists
        context_file = os.path.join(win_path, "Context.txt")
        file_exists = os.path.exists(context_file)
        
        if file_exists:
            print(f"\n⚠️  Context.txt already exists at: {context_file}")
            
            # Show existing content preview
            try:
                with open(context_file, "r", encoding="utf-8") as f:
                    existing_content = f.read()
                
                # Show preview (first 500 chars)
                preview_length = 500
                if len(existing_content) > preview_length:
                    preview = existing_content[:preview_length] + "\n... [truncated]"
                else:
                    preview = existing_content
                
                print("\n--- Existing Content Preview ---")
                print(preview)
                print("--- End Preview ---\n")
                
            except Exception as e:
                print(f"   (Could not read existing content: {e})")
            
            append_choice = input("Append new entry to existing file? (y/n): ").strip().lower()
            if append_choice not in ['y', 'yes']:
                print("❌ Operation cancelled")
                return False
            
            print("✓ New content will be appended with timestamp separator")
        
        # =========================================================================
        # Step b) Ask for input method
        # =========================================================================
        print("\nHow would you like to provide the context?")
        print("[1] Manual text input")
        print("[2] Audio transcription (60 seconds max)")
        
        method_choice = input("\nEnter choice (1/2): ").strip()
        
        context_text = ""
        
        if method_choice == '1':
            # -----------------------------------------------------------------
            # Option i) Manual multi-line text input
            # -----------------------------------------------------------------
            print("\n" + "-" * 70)
            print("Enter your context/description below.")
            print("Press Enter on an empty line to finish.")
            print("-" * 70 + "\n")
            
            lines = []
            while True:
                try:
                    line = input()
                    if line == "":
                        # Empty line - finish input
                        break
                    lines.append(line)
                except EOFError:
                    break
            
            context_text = "\n".join(lines)
            
            if not context_text.strip():
                print("❌ No text entered")
                return False
            
            print("\n" + "-" * 70)
            print("📝 Your input:")
            print("-" * 70)
            print(context_text)
            print("-" * 70)
            
        elif method_choice == '2':
            # -----------------------------------------------------------------
            # Option ii) Audio transcription with 60s recording & countdown
            # -----------------------------------------------------------------
            try:
                import sounddevice as sd
                import numpy as np
                from faster_whisper import WhisperModel
            except ImportError as e:
                print(f"\n❌ Required libraries not installed: {e}")
                print("   Please install:")
                print("   pip install sounddevice numpy faster-whisper")
                return False
            
            print("\n📥 Loading Whisper model (this may take a moment)...")
            try:
                model = WhisperModel("small", device="cpu", compute_type="int8")
                print("✅ Model loaded!")
            except Exception as e:
                print(f"❌ Failed to load Whisper model: {e}")
                return False
            
            sample_rate = 16000
            max_duration = 60  # Maximum recording duration
            
            print(f"\n🎤 Audio Recording Setup:")
            print(f"   Maximum duration: {max_duration} seconds")
            print(f"   Sample rate: {sample_rate} Hz")
            print(f"\n   ➡️  Press Enter at any time to stop recording early.")
            
            input(f"\nPress Enter to START recording...")
            
            # -----------------------------------------------------------------
            # Recording with countdown and early stop capability
            # -----------------------------------------------------------------
            
            # Shared state for threading
            audio_buffer = []
            stop_recording = threading.Event()
            recording_error = [None]
            
            def audio_callback(indata, frames, time_info, status):
                """Callback function for audio stream."""
                if status:
                    recording_error[0] = status
                if not stop_recording.is_set():
                    audio_buffer.append(indata.copy())
            
            def wait_for_enter():
                """Wait for Enter key press in separate thread."""
                try:
                    input()
                    stop_recording.set()
                except:
                    pass
            
            # Start the Enter key listener thread
            enter_thread = threading.Thread(target=wait_for_enter, daemon=True)
            enter_thread.start()
            
            print(f"\n🎤 Recording... Speak now! (Press Enter to stop early)")
            print("-" * 70)
            
            try:
                with sd.InputStream(samplerate=sample_rate, channels=1, 
                                    dtype=np.float32, callback=audio_callback):
                    
                    # Countdown loop
                    for remaining in range(max_duration, 0, -1):
                        if stop_recording.is_set():
                            elapsed = max_duration - remaining
                            print(f"\r⏹️  Recording stopped by user at {elapsed}s" + " " * 30)
                            break
                        
                        # Display countdown timer with progress bar
                        bar_length = 30
                        filled = int(bar_length * (max_duration - remaining) / max_duration)
                        bar = "█" * filled + "░" * (bar_length - filled)
                        print(f"\r   ⏱️  Time remaining: {remaining:2d}s  [{bar}]", end="", flush=True)
                        
                        time.sleep(1)
                    else:
                        # Loop completed without break (full 60 seconds)
                        stop_recording.set()
                        print(f"\r✅ Recording complete! (Full {max_duration}s)" + " " * 30)
                
            except Exception as e:
                print(f"\n❌ Recording failed: {e}")
                return False
            
            print("-" * 70)
            
            # Check if we got any audio
            if not audio_buffer:
                print("❌ No audio recorded")
                return False
            
            # Combine audio chunks
            audio = np.concatenate(audio_buffer, axis=0).flatten()
            duration_recorded = len(audio) / sample_rate
            print(f"✓ Recorded {duration_recorded:.1f} seconds of audio")
            
            if recording_error[0]:
                print(f"⚠️  Recording warning: {recording_error[0]}")
            
            # Transcribe
            print("🔄 Transcribing...")
            try:
                segments, info = model.transcribe(audio, beam_size=5)
                print(f"   Detected language: {info.language} (probability: {info.language_probability:.2f})")
                
                context_text = " ".join([segment.text for segment in segments]).strip()
            except Exception as e:
                print(f"❌ Transcription failed: {e}")
                return False
            
            if not context_text:
                print("⚠️  No speech detected. Please try again.")
                return False
            
            print("\n" + "-" * 70)
            print("📝 Transcription result:")
            print("-" * 70)
            print(context_text)
            print("-" * 70)
            
            # Allow user to confirm or cancel
            confirm = input("\nSave this transcription? (y/n): ").strip().lower()
            if confirm not in ['y', 'yes']:
                print("❌ Operation cancelled")
                return False
                
        else:
            print("❌ Invalid choice")
            return False
        
        # =========================================================================
        # Save to Context.txt (append with timestamp)
        # =========================================================================
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            input_method = "Manual Entry" if method_choice == '1' else "Audio Transcription"
            
            with open(context_file, "a", encoding="utf-8") as f:
                # Add separator if appending to existing file
                if file_exists:
                    f.write("\n")
                
                # Write timestamped entry
                f.write("=" * 50 + "\n")
                f.write(f"[{timestamp}] - {input_method}\n")
                f.write("=" * 50 + "\n\n")
                f.write(f"{context_text}\n")
            
            if file_exists:
                print(f"\n✓ Context appended to: {context_file}")
            else:
                print(f"\n✓ Context saved to: {context_file}")
            
            return True
            
        except PermissionError:
            print(f"❌ Permission denied: Cannot write to {context_file}")
            return False
        except Exception as e:
            print(f"❌ Failed to save context: {e}")
            return False


    # =====================================================================
    # Non-interactive (CLI) methods — used by the agent via shell_run
    # =====================================================================

    def cli_list_jobs(self, status=None, owner=None, sort_by="JobID"):
        self.all_jobs = []
        for server in self.servers:
            command = f"python3 {self.script_path} --json"
            output = self.connect_and_execute(server, command)
            if output:
                jobs = self.parse_output(output, server['name'])
                self.all_jobs.extend(jobs)

        jobs = self.all_jobs
        if status:
            jobs = [j for j in jobs if j['Status'] == status.upper()]
        if owner:
            jobs = [j for j in jobs if j['Owner'] == owner]

        if sort_by in ['CPUs']:
            jobs = sorted(jobs, key=lambda x: int(x[sort_by]) if x[sort_by].isdigit() else 0)
        else:
            jobs = sorted(jobs, key=lambda x: x.get(sort_by, ''))

        return json.dumps(jobs, indent=2)

    def cli_submit_job(self, win_path, script_name="qsubrunfhgfs.sh"):
        win_path = os.path.abspath(win_path)
        server_hostname, job_path = self.windows_to_linux_path(win_path)

        if not server_hostname:
            drive_letters = ', '.join(self.drive_mapping.keys())
            return json.dumps({"error": f"Path is not on a mapped drive ({drive_letters})"})

        server = None
        for s in self.servers:
            if s['hostname'] == server_hostname:
                server = s
                break

        if not server:
            return json.dumps({"error": f"Server {server_hostname} not found in configuration"})

        command = f"cd {job_path} && {self.qsub_path} {script_name}"
        output = self.connect_and_execute(server, command)

        if output:
            return json.dumps({
                "status": "submitted",
                "server": server['name'],
                "linux_path": job_path,
                "output": output.strip(),
            })
        return json.dumps({"error": "Job submission failed — no response from server"})

    def cli_kill_job(self, job_id, delete_dir=False):
        self.cli_list_jobs()

        matched_job = None
        for job in self.all_jobs:
            if job_id in job['JobID']:
                matched_job = job
                break

        if not matched_job:
            return json.dumps({"error": f"Job ID '{job_id}' not found"})

        server_name = matched_job['Server']
        server = None
        for s in self.servers:
            if s['name'] == server_name:
                server = s
                break

        if not server:
            return json.dumps({"error": f"Server {server_name} not found in configuration"})

        command = f"{self.qdel_path} {job_id}"
        output = self.connect_and_execute(server, command)

        if output is None:
            return json.dumps({"error": f"Failed to kill job {job_id}"})

        result = {"status": "killed", "job_id": job_id, "server": server_name}

        if delete_dir and matched_job.get('Job_Path'):
            sim_path = matched_job['Job_Path'] + "/Simulation"
            del_cmd = f"rm -rf {sim_path}"
            self.connect_and_execute(server, del_cmd)
            result["deleted_directory"] = sim_path

        return json.dumps(result)

    def cli_job_log(self, job_id, lines=50):
        self.cli_list_jobs()

        matched_job = None
        for job in self.all_jobs:
            if job_id in job['JobID'] or job['JobID'].startswith(job_id + '.'):
                matched_job = job
                break

        if not matched_job:
            return json.dumps({"error": f"Job ID '{job_id}' not found"})

        server_name = matched_job['Server']
        job_path = matched_job['Job_Path']
        log_file = f"{job_path}/Simulation/messag"

        server = None
        for s in self.servers:
            if s['name'] == server_name:
                server = s
                break

        if not server:
            return json.dumps({"error": f"Server {server_name} not found"})

        check_cmd = f"test -f {log_file} && echo 'EXISTS' || echo 'NOT_FOUND'"
        result = self.connect_and_execute(server, check_cmd)

        if not result or 'NOT_FOUND' in result:
            return json.dumps({"error": f"Log file not found: {log_file}"})

        tail_cmd = f"tail -n {int(lines)} {log_file}"
        output = self.connect_and_execute(server, tail_cmd)

        return json.dumps({
            "job_id": matched_job['JobID'],
            "job_name": matched_job['Job_Name'],
            "server": server_name,
            "log_file": log_file,
            "lines": output or "",
        })

    def cli_generate_report(self, win_path):
        win_path = os.path.abspath(win_path)

        if not os.path.exists(win_path):
            return json.dumps({"error": f"Windows path does not exist: {win_path}"})

        server_hostname, linux_path = self.windows_to_linux_path(win_path)

        if not server_hostname:
            drive_letters = ', '.join(self.drive_mapping.keys())
            return json.dumps({"error": f"Path is not on a mapped drive ({drive_letters})"})

        server = None
        for s in self.servers:
            if s['hostname'] == server_hostname:
                server = s
                break

        if not server:
            return json.dumps({"error": f"Server {server_hostname} not found in configuration"})

        check_cmd = 'bash -i -c "which run_report_win.sh 2>/dev/null || command -v run_report_win.sh 2>/dev/null"'
        result = self.connect_and_execute(server, check_cmd)

        if not result or not result.strip():
            return json.dumps({"error": "'run_report_win.sh' not found in server's PATH"})

        report_cmd = f'bash -i -c \'run_report_win.sh "{linux_path}"\''
        output = self.connect_and_execute(server, report_cmd)

        if output is None:
            return json.dumps({"error": "Report generation failed — no response from server"})

        return json.dumps({
            "status": "completed",
            "server": server['name'],
            "linux_path": linux_path,
            "windows_path": win_path,
            "output": output.strip(),
        })


def get_python_path(config):
    configured = config.get('python_path', '').strip()
    if configured:
        return configured
    if sys.platform == 'win32':
        return 'python'
    return 'python3'


def print_menu():
    """Print the main menu"""
    print("=" * 70)
    print("         PBS PRO Job Monitor & Submitter")
    print("=" * 70)
    print("\n[1] Display All Jobs")
    print("[2] Display Jobs (Sorted)")
    print("[3] Filter Jobs by Status")
    print("[4] Filter Jobs by Owner")
    print("[5] Submit New Job")
    print("[6] Kill Job")
    print("[7] View Job Log (tail -f)")
    print("[8] Refresh Job List")
    print("[9] Document Job Description") 
    print("[10] Generate Report")
    print("[0] Exit")
    print("\n" + "=" * 70)


def get_sort_menu():
    """Get sorting preference"""
    print("\nSort by:")
    print("[1] JobID")
    print("[2] Job Name")
    print("[3] CPUs")
    print("[4] Status")
    print("[5] Owner")
    print("[6] Server")
    print("[7] Memory")
    
    choice = input("\nEnter choice: ").strip()
    
    sort_map = {
        '1': 'JobID',
        '2': 'Job_Name',
        '3': 'CPUs',
        '4': 'Status',
        '5': 'Owner',
        '6': 'Server',
        '7': 'Memory'
    }
    
    return sort_map.get(choice, 'JobID')


def _build_cli_parser():
    import argparse
    parser = argparse.ArgumentParser(
        description="PBS PRO Job Monitor & Submitter",
        prog="WindowsPBS.py",
    )
    parser.add_argument("-c", "--config", default=None, help="Path to configuration YAML file")

    sub = parser.add_subparsers(dest="command")

    # -- list --
    p_list = sub.add_parser("list", help="List jobs across all servers (JSON output)")
    p_list.add_argument("--status", default=None, help="Filter by status (R or Q)")
    p_list.add_argument("--owner", default=None, help="Filter by owner username")
    p_list.add_argument("--sort", default="JobID", help="Sort field (JobID, Job_Name, CPUs, Status, Owner, Server, Memory)")

    # -- submit --
    p_submit = sub.add_parser("submit", help="Submit a job")
    p_submit.add_argument("--path", required=True, help="Windows path to the job directory")
    p_submit.add_argument("--script", default="qsubrunfhgfs.sh", help="Job script name (default: qsubrunfhgfs.sh)")

    # -- kill --
    p_kill = sub.add_parser("kill", help="Kill a running job")
    p_kill.add_argument("--job-id", required=True, help="Job ID (or partial ID)")
    p_kill.add_argument("--delete-dir", action="store_true", help="Also delete the job's Simulation directory")

    # -- log --
    p_log = sub.add_parser("log", help="Get last N lines of a job's log file")
    p_log.add_argument("--job-id", required=True, help="Job ID (or partial ID)")
    p_log.add_argument("--lines", type=int, default=50, help="Number of lines to retrieve (default: 50)")

    # -- report --
    p_report = sub.add_parser("report", help="Generate a report for a simulation directory")
    p_report.add_argument("--path", required=True, help="Windows path to the job directory")

    return parser


def _init_manager(config_path=None):
    config = load_config(config_path)
    validate_config(config)
    return PBSJobManager(config, getuser())


def main_cli(args):
    manager = _init_manager(args.config)

    if args.command == "list":
        print(manager.cli_list_jobs(status=args.status, owner=args.owner, sort_by=args.sort))
    elif args.command == "submit":
        print(manager.cli_submit_job(args.path, script_name=args.script))
    elif args.command == "kill":
        print(manager.cli_kill_job(args.job_id, delete_dir=args.delete_dir))
    elif args.command == "log":
        print(manager.cli_job_log(args.job_id, lines=args.lines))
    elif args.command == "report":
        print(manager.cli_generate_report(args.path))
    else:
        _build_cli_parser().print_help()


def main_interactive(config_path=None):
    print("\n" + "=" * 70)
    print("         PBS PRO Job Monitor - Startup")
    print("=" * 70 + "\n")

    manager = _init_manager(config_path)

    print(f"\n👤 User: {manager.user}")
    print(f"📂 Remote script path: {manager.script_path}")
    print(f"📁 Linux base path: {manager.linux_base_path}")

    print(f"\n🖥️  Configured servers:")
    for srv in manager.servers:
        drive = manager.server_to_drive.get(srv['hostname'], '?')
        print(f"   - {srv['name']} ({srv['hostname']}) -> Drive {drive}:")

    manager.fetch_all_jobs()

    while True:
        print_menu()
        choice = input("Enter your choice: ").strip()

        if choice == '0':
            print("\n👋 Goodbye!\n")
            break
        elif choice == '1':
            manager.display_jobs()
        elif choice == '2':
            sort_by = get_sort_menu()
            manager.display_jobs(sort_by=sort_by)
        elif choice == '3':
            status = input("\nEnter status (R/Q): ").strip().upper()
            filtered = [j for j in manager.all_jobs if j['Status'] == status]
            print(f"\n📋 Jobs with status '{status}':\n")
            manager.display_jobs(filtered)
        elif choice == '4':
            owner = input("\nEnter owner username: ").strip()
            filtered = [j for j in manager.all_jobs if j['Owner'] == owner]
            print(f"\n📋 Jobs owned by '{owner}':\n")
            manager.display_jobs(filtered)
        elif choice == '5':
            manager.submit_job_interactive()
        elif choice == '6':
            print("\n" + "=" * 70)
            print("                     Kill Job")
            print("=" * 70)
            job_id = input("\nEnter Job ID (or partial ID): ").strip()
            if manager.kill_job(job_id):
                input("\nPress Enter to refresh job list...")
                manager.fetch_all_jobs()
                manager.display_jobs()
        elif choice == '7':
            manager.display_jobs()
            print("\n" + "=" * 70)
            print("                     View Job Log")
            print("=" * 70)
            job_id = input("\nEnter Job ID (or partial ID): ").strip()
            manager.view_log(job_id)
        elif choice == '8':
            manager.fetch_all_jobs()
            manager.display_jobs()
        elif choice == '9':
            manager.document_job_description()
        elif choice == '10':
            manager.generate_report_interactive()
        else:
            print("\n❌ Invalid choice. Please try again.\n")

        input("\nPress Enter to continue...")
        print("\n" * 2)


if __name__ == "__main__":
    try:
        parser = _build_cli_parser()
        args = parser.parse_args()

        if args.command:
            main_cli(args)
        else:
            main_interactive(args.config)
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user. Goodbye!\n")
        sys.exit(0)