#!/usr/bin/env python3
import datetime
import glob
import json
import logging
import os
import shutil
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


# Simple tooltip class
class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.tooltip_window = None

    def enter(self, event=None):
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")

        label = tk.Label(
            self.tooltip_window,
            text=self.text,
            background="lightyellow",
            relief="solid",
            borderwidth=1,
            wraplength=300,
        )
        label.pack()

    def leave(self, event=None):
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


class ConfigManager:
    """Handles encrypted storage and retrieval of Plex settings"""
    
    def __init__(self):
        self.config_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_file = os.path.join(self.config_dir, "plex_config.enc")
        self.key_file = os.path.join(self.config_dir, "plex_config.key")
    
    def _generate_key(self, password: str) -> bytes:
        """Generate encryption key from password"""       
        password_bytes = password.encode()
        # Use a fixed salt for consistency (in production, use random salt)
        salt = b'plexsyncer_salt_12345'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password_bytes))
        return key
    
    def _get_machine_key(self) -> bytes:
        """Generate a machine-specific key for encryption"""
        # Use machine-specific info for key generation
        import platform
        machine_info = f"{platform.node()}{platform.system()}{platform.release()}"
        return self._generate_key(machine_info)
    
    def save_config(self, config_data: dict) -> bool:
        """Save encrypted configuration data"""
        try:
            json_data = json.dumps(config_data)

            # Use proper encryption
            key = self._get_machine_key()
            fernet = Fernet(key)
            encrypted_data = fernet.encrypt(json_data.encode())

            # Save to file
            with open(self.config_file, 'wb') as f:
                f.write(encrypted_data)
            
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False
    
    def load_config(self) -> dict:
        """Load and decrypt configuration data"""
        try:
            if not os.path.exists(self.config_file):
                return {}
            
            # Read the file
            with open(self.config_file, 'rb') as f:
                encrypted_data = f.read()
            
            # Use proper decryption
            key = self._get_machine_key()
            fernet = Fernet(key)
            decrypted_data = fernet.decrypt(encrypted_data)
    
            
            config_data = json.loads(decrypted_data.decode())
            return config_data
        except Exception as e:
            print(f"Error loading config: {e}")
            return {}
    
    def config_exists(self) -> bool:
        """Check if config file exists"""
        return os.path.exists(self.config_file)
    
    def delete_config(self) -> bool:
        """Delete the configuration file"""
        try:
            if os.path.exists(self.config_file):
                os.remove(self.config_file)
            return True
        except Exception as e:
            print(f"Error deleting config: {e}")
            return False


from integrations.spotify import SpotifyIntegration
from integrations.tidal import TidalIntegration
from plexsyncer.api import (
    compare_m3u8_to_plex_playlist,
    debug_library_content,
    get_section_id_from_library,
    test_plex_connection,
    upload_playlist_via_api,
    upload_playlist_via_http_api,
    verify_local_playlists_content_in_plex,
    verify_uploaded_playlists,
)

# Import core functionality
from plexsyncer.playlist import generate_master_playlist, process_library

class PlexSyncerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PlexSyncer GUI - Music Playlist Manager")
        self.geometry("1000x700")
        self.minsize(800, 600)  # Reduced minimum size for smaller screens
        self.generated_files = []
        
        # Initialize config manager
        self.config_manager = ConfigManager()
        self.plex_config = self.config_manager.load_config()

        # Configure main window
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Create main notebook
        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)  # Reduced padding

        # Create tabs
        self.local_tab = ttk.Frame(notebook)
        self.plex_tab = ttk.Frame(notebook)
        self.spotify_tab = ttk.Frame(notebook)
        self.tidal_tab = ttk.Frame(notebook)

        notebook.add(self.local_tab, text="📁 Local Playlists")
        notebook.add(self.plex_tab, text="🎵 Plex Upload")
        notebook.add(self.spotify_tab, text="🎧 Spotify Integration")
        notebook.add(self.tidal_tab, text="🌊 Tidal Integration")

        self._build_local_tab()
        self._build_plex_tab()
        self._build_spotify_tab()
        self._build_tidal_tab()

    def _build_local_tab(self):
        """Build the local tab with playlist generation and management controls"""
        frame = self.local_tab
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(4, weight=1)  # Console output row expands

        # === PLAYLIST GENERATION ===
        gen_frame = ttk.LabelFrame(frame, text="⚙️ Configuration", padding=6)
        gen_frame.grid(row=0, column=0, sticky="ew", padx=3, pady=2)
        gen_frame.grid_columnconfigure(1, weight=1)

        # Playlist folder selection
        ttk.Label(gen_frame, text="Playlist folder (on your PC):", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=2
        )

        folder_frame = ttk.Frame(gen_frame)
        folder_frame.grid(row=0, column=1, sticky="ew", pady=2)
        folder_frame.grid_columnconfigure(0, weight=1)

        self.playlist_folder_var = tk.StringVar()
        ttk.Entry(folder_frame, textvariable=self.playlist_folder_var, font=("", 9)).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        ttk.Button(folder_frame, text="📁", command=self._browse_playlist_folder, width=3).pack(
            side="right"
        )

        # Plex root path
        ttk.Label(gen_frame, text="Playlist folder (on plex server):", font=("", 9, "bold")).grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=2
        )

        plex_root_frame = ttk.Frame(gen_frame)
        plex_root_frame.grid(row=2, column=1, sticky="ew", pady=2)
        plex_root_frame.grid_columnconfigure(0, weight=1)

        self.plex_root_var = tk.StringVar()
        ttk.Entry(plex_root_frame, textvariable=self.plex_root_var, font=("", 9)).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        ttk.Button(plex_root_frame, text="📁", command=self._browse_plex_root, width=3).pack(
            side="right"
        )

        # Options frame
        options_frame = ttk.Frame(gen_frame)
        options_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        # Encode spaces is always enabled (no option to disable)
        self.encode_spaces_var = tk.BooleanVar(value=True)

        # Incremental mode option
        self.incremental_var = tk.BooleanVar(value=False)
        incremental_checkbox = ttk.Checkbutton(
            options_frame,
            text="📝 Incremental",
            variable=self.incremental_var,
        )
        incremental_checkbox.pack(side="left", padx=(0, 15))
        
        ToolTip(
            incremental_checkbox,
            "Only process files that have been modified since last run.\n\n"
            "Benefits:\n"
            "• Faster processing for large libraries\n"
            "• Skips unchanged playlists\n"
            "• Preserves existing files\n\n"
            "When to use:\n"
            "• Regular updates to existing playlists\n"
            "• Large libraries with few changes\n"
            "• Maintaining existing playlist structure"
        )

        # Parallel processing option
        self.parallel_processing_var = tk.BooleanVar(value=True)
        parallel_checkbox = ttk.Checkbutton(
            options_frame,
            text="🚀 Parallel",
            variable=self.parallel_processing_var,
        )
        parallel_checkbox.pack(side="left")
        
        # Add tooltip for parallel processing
        ToolTip(
            parallel_checkbox,
            "Enable parallel processing for faster playlist generation.\n\n"
            "Benefits:\n"
            "• 2-4x faster processing for large libraries\n"
            "• Utilizes multiple CPU cores\n"
            "• Automatic worker scaling\n\n"
            "When to disable:\n"
            "• Small libraries (< 5 playlists)\n"
            "• Memory-constrained systems\n"
            "• Debugging issues"
        )

        # Buttons frame
        buttons_frame = ttk.Frame(gen_frame)
        buttons_frame.grid(row=4, column=0, columnspan=2, pady=(6, 0))

        # First row of buttons
        buttons_row1 = ttk.Frame(buttons_frame)
        buttons_row1.grid(row=0, column=0, sticky="ew", pady=(0, 3))
        buttons_row1.grid_columnconfigure(0, weight=1)
        buttons_row1.grid_columnconfigure(1, weight=1)

        generate_btn = ttk.Button(
            buttons_row1,
            text="📝 Generate",
            command=self._run_process_library,
        )
        generate_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        
        ToolTip(
            generate_btn,
            "Generate playlist files from your music folders.\n\n"
            "Process:\n"
            "• Scans each folder for audio files\n"
            "• Creates M3U files with proper paths\n"
            "• Extracts metadata (duration, title)\n"
            "• Remaps paths for Plex compatibility\n"
            "• Automatically encodes spaces as %20\n\n"
            "Options:\n"
            "• Parallel processing for speed\n"
            "• Incremental mode for updates\n"
            "• HTTP API compatible format"
        )

        autodiscover_btn = ttk.Button(
            buttons_row1,
            text="🔄 Discover",
            command=self._auto_discover_files,
        )
        autodiscover_btn.grid(row=0, column=1, sticky="ew", padx=(2, 0))
        
        ToolTip(
            autodiscover_btn,
            "Automatically find and add existing M3U/M3U files.\n\n"
            "Searches for:\n"
            "• .m3u files (for HTTP API upload)\n"
            "• .m3u8 files (converted to .m3u for upload)\n"
            "• Files in subdirectories\n\n"
            "Use when:\n"
            "• You have existing playlist files\n"
            "• After manual playlist creation\n"
            "• To refresh the file list"
        )

        # === PROCESSING STATUS ===
        status_frame = ttk.LabelFrame(frame, text="📊 Status", padding=6)
        status_frame.grid(row=1, column=0, sticky="ew", padx=3, pady=2)
        status_frame.grid_columnconfigure(0, weight=1)

        # Progress bar
        self.local_progress_var = tk.DoubleVar()
        self.local_progress_bar = ttk.Progressbar(
            status_frame, 
            variable=self.local_progress_var, 
            maximum=100, 
            length=300,
            mode='determinate'
        )
        self.local_progress_bar.grid(row=0, column=0, sticky="ew", pady=(0, 3))

        # Status text
        self.local_status_var = tk.StringVar(value="Ready to process playlists")
        self.local_status_label = ttk.Label(
            status_frame, 
            textvariable=self.local_status_var, 
            foreground="blue",
            font=("", 8)
        )
        self.local_status_label.grid(row=1, column=0, pady=(0, 3))

        # Processing statistics
        self.local_stats_var = tk.StringVar(value="No processing statistics available")
        self.local_stats_label = ttk.Label(
            status_frame, 
            textvariable=self.local_stats_var, 
            foreground="gray",
            font=("", 8)
        )
        self.local_stats_label.grid(row=2, column=0)

        # === PLAYLIST MANAGEMENT ===
        mgmt_frame = ttk.LabelFrame(frame, text="📋 Playlists", padding=6)
        mgmt_frame.grid(row=2, column=0, sticky="ew", padx=3, pady=2)
        mgmt_frame.grid_columnconfigure(0, weight=1)

        # File list
        list_frame = ttk.Frame(mgmt_frame)
        list_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        list_frame.grid_columnconfigure(0, weight=1)

        self.file_listbox = tk.Listbox(list_frame, height=8, font=("", 9))
        self.file_listbox.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.file_listbox.config(yscrollcommand=scrollbar.set)

        # File buttons
        file_buttons = ttk.Frame(mgmt_frame)
        file_buttons.grid(row=1, column=0, sticky="ew")

        ttk.Button(file_buttons, text="➕ Add Files", command=self._add_m3u8_files).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(file_buttons, text="� Add Folder", command=self._add_m3u8_folder).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(file_buttons, text="� Preview", command=self._preview_selected_file).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(file_buttons, text="➖ Remove", command=self._remove_selected_files).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(file_buttons, text="🗑️ Clear All", command=self._clear_all_files).pack(
            side="left", padx=(0, 5)
        )

        # === CLEANUP TOOLS ===
        cleanup_frame = ttk.LabelFrame(frame, text="🧹 Tools", padding=6)
        cleanup_frame.grid(row=3, column=0, sticky="ew", padx=3, pady=1)
        cleanup_frame.grid_columnconfigure(0, weight=1)

        # First row of cleanup buttons
        cleanup_row1 = ttk.Frame(cleanup_frame)
        cleanup_row1.grid(row=1, column=0, sticky="ew")

        ttk.Button(
            cleanup_row1,
            text="🧹 Clean M3U",
            command=self._cleanup_m3u_files,
        ).pack(side="left", padx=(0, 1))

        ttk.Button(
            cleanup_row1,
            text="🗑️ Delete",
            command=self._cleanup_current_selection,
        ).pack(side="left", padx=(0, 1))

        ttk.Button(
            cleanup_row1,
            text="🔄 Refresh",
            command=self._refresh_file_lists,
        ).pack(side="left", padx=(0, 1))

        ttk.Button(
            cleanup_row1,
            text="� Quick Fix",
            command=self._quick_fix_common_issues,
        ).pack(side="left", padx=(0, 1))
        
        # === CONSOLE OUTPUT ===
        console_frame = ttk.LabelFrame(frame, text="📜 Console", padding=6)
        console_frame.grid(row=4, column=0, sticky="nsew", padx=3, pady=2)
        console_frame.grid_rowconfigure(0, weight=1)
        console_frame.grid_columnconfigure(0, weight=1)

        # Console text area
        console_text_frame = ttk.Frame(console_frame)
        console_text_frame.grid(row=0, column=0, sticky="nsew")
        console_text_frame.grid_rowconfigure(0, weight=1)
        console_text_frame.grid_columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            console_text_frame, height=6, wrap=tk.WORD, font=("Consolas", 8)
        )
        log_scrollbar = ttk.Scrollbar(console_text_frame, orient="vertical")
        self.log_text.config(yscrollcommand=log_scrollbar.set)
        log_scrollbar.config(command=self.log_text.yview)

        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar.grid(row=0, column=1, sticky="ns")

        # Log control buttons
        log_controls = ttk.Frame(console_frame)
        log_controls.grid(row=1, column=0, pady=(3, 0))
        log_controls.grid_columnconfigure(0, weight=1)
        log_controls.grid_columnconfigure(1, weight=1)

        ttk.Button(log_controls, text="💾 Save", command=self._save_logs).grid(
            row=0, column=0, sticky="ew", padx=(0, 2)
        )
        ttk.Button(log_controls, text="🗑️ Clear", command=self._clear_logs).grid(
            row=0, column=1, sticky="ew", padx=(2, 0)
        )

    def _build_plex_tab(self):
        """Build the Plex upload tab with connection and upload controls"""
        frame = self.plex_tab
        frame.grid_columnconfigure(0, weight=1)

        # === CONNECTION SETTINGS ===
        conn_frame = ttk.LabelFrame(frame, text="🔗 Plex Connection", padding=10)
        conn_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        conn_frame.grid_columnconfigure(1, weight=1)

        # Plex URL
        ttk.Label(conn_frame, text="Plex URL:", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8), padx=(0, 10)
        )
        self.plex_url_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.plex_url_var, font=("", 9)).grid(
            row=0, column=1, sticky="ew", pady=(0, 8)
        )

        # Token
        ttk.Label(conn_frame, text="Token:", font=("", 9, "bold")).grid(
            row=1, column=0, sticky="w", pady=(0, 8), padx=(0, 10)
        )
        self.plex_token_var = tk.StringVar()
        ttk.Entry(
            conn_frame, textvariable=self.plex_token_var, show="*", font=("", 9)
        ).grid(row=1, column=1, sticky="ew", pady=(0, 8))

        # Section ID
        ttk.Label(conn_frame, text="Section ID:", font=("", 9, "bold")).grid(
            row=2, column=0, sticky="w", padx=(0, 10)
        )
        section_frame = ttk.Frame(conn_frame)
        section_frame.grid(row=2, column=1, sticky="ew")
        section_frame.grid_columnconfigure(0, weight=1)

        self.section_id_var = tk.StringVar()
        ttk.Entry(
            section_frame, textvariable=self.section_id_var, width=15, font=("", 9)
        ).pack(side="left", padx=(0, 10))
        ttk.Button(section_frame, text="🔍 Fetch", command=self._fetch_section_id).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(
            section_frame, text="🧪 Test Connection", command=self._test_plex_connection
        ).pack(side="left")

        # SSL Verification option
        ssl_frame = ttk.Frame(conn_frame)
        ssl_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        
        self.disable_ssl_verification_var = tk.BooleanVar()
        ssl_checkbox = ttk.Checkbutton(
            ssl_frame,
            text="🔓 Disable SSL certificate verification",
            variable=self.disable_ssl_verification_var,
        )
        ssl_checkbox.pack(side="left")
        
        # Add tooltip for SSL option
        ToolTip(
            ssl_checkbox,
            "Enable this if you get SSL certificate errors when connecting to Plex.\n"
            "This disables SSL certificate verification for HTTPS connections.\n"
            "Only use this if you trust your Plex server and network.\n\n"
            "Common SSL errors:\n"
            "• Certificate verify failed\n"
            "• IP address mismatch\n"
            "• Self-signed certificate"
        )

        # Configuration save/load buttons
        config_frame = ttk.Frame(conn_frame)
        config_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        
        save_btn = ttk.Button(
            config_frame, 
            text="💾 Save Settings", 
            command=self._save_plex_config
        )
        save_btn.pack(side="left", padx=(0, 10))
        
        ToolTip(
            save_btn,
            "Save current Plex settings to an encrypted config file.\n\n"
            "Saved settings include:\n"
            "• Plex URL and token\n"
            "• Section ID\n"
            "• SSL verification setting\n"
            "• Playlist and root folder paths\n"
            "• Processing options\n\n"
            "Settings are encrypted using machine-specific keys\n"
            "and stored securely in the runtime directory."
        )
        
        load_btn = ttk.Button(
            config_frame, 
            text="📂 Load Settings", 
            command=self._load_plex_config
        )
        load_btn.pack(side="left", padx=(0, 10))
        
        ToolTip(
            load_btn,
            "Load previously saved Plex settings from encrypted config file.\n\n"
            "This will restore:\n"
            "• Connection settings (URL, token, section ID)\n"
            "• SSL verification setting\n"
            "• Folder paths\n"
            "• Processing preferences\n\n"
            "Settings are automatically loaded when the app starts."
        )
        
        if self.config_manager.config_exists():
            clear_btn = ttk.Button(
                config_frame, 
                text="🗑️ Clear Settings", 
                command=self._clear_plex_config
            )
            clear_btn.pack(side="left", padx=(0, 10))
            
            ToolTip(
                clear_btn,
                "Delete all saved Plex settings and clear the config file.\n\n"
                "This will:\n"
                "• Remove the encrypted config file\n"
                "• Clear all current form values\n"
                "• Require re-entering all settings\n\n"
                "This action cannot be undone!"
            )
        
        # Load saved settings on startup
        self._load_plex_config()

        # === UPLOAD SECTION ===
        upload_frame = ttk.LabelFrame(frame, text="⬆️ Upload Playlists", padding=10)
        upload_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        upload_frame.grid_columnconfigure(0, weight=1)

        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            upload_frame, variable=self.progress_var, maximum=100, length=300
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        # Status
        self.status_var = tk.StringVar(value="Ready to upload")
        status_label = ttk.Label(
            upload_frame, textvariable=self.status_var, foreground="blue"
        )
        status_label.grid(row=1, column=0, pady=(0, 10))


        # Upload button
        upload_btn = ttk.Button(
            upload_frame, text="⬆️ Upload Playlists", command=self._upload_playlists
        )
        upload_btn.grid(row=3, column=0, pady=(10, 0))
        
        # Add tooltip to the upload button
        ToolTip(
            upload_btn,
            "Upload selected playlists to your Plex server.\n\n"
            "Requirements:\n"
            "• Valid Plex URL and token\n"
            "• Section ID for your music library\n"
            "• Correctly configured Local and Plex root paths\n\n"
            "HTTP API method references existing playlist files on the Plex server\n"
            "and instructs Plex to import them using the /playlists/upload endpoint.\n"
            "The playlist files must already exist on the server at the specified path.\n"
            "This method requires proper path mapping between local and server paths.",
        )

        # === VERIFICATION TOOLS ===
        verify_frame = ttk.LabelFrame(frame, text="✅ Verification Tools", padding=10)
        verify_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)

        verify_buttons = ttk.Frame(verify_frame)
        verify_buttons.pack()

        verify_uploads_btn = ttk.Button(
            verify_buttons, text="✅ Verify Uploads", command=self._verify_uploads
        )
        verify_uploads_btn.pack(side="left", padx=(0, 10))

        ToolTip(
            verify_uploads_btn,
            "Verify that the uploaded playlists exist on the Plex server.\n\n"
            "Checks:\n"
            "• Playlist names matches the playlist file name\n"
            "Use this tool after uploading playlists to ensure they are properly imported."
        )

        verify_content_btn = ttk.Button(
            verify_buttons, text="🔍 Verify Content", command=self._verify_content
        )
        verify_content_btn.pack(side="left", padx=(0, 10))

        ToolTip(
            verify_content_btn,
            "Verify the content of playlists uploaded to Plex.\n\n"
            "Checks:\n"
            "• Track names and paths match between local playlists and Plex playlists\n"
            "• Identifies missing or extra tracks in Plex playlists\n\n"
            "Use this tool to ensure the uploaded playlists are accurate and complete."
        )
        
        compare_btn = ttk.Button(
            verify_buttons, text="🔄 Compare Playlist", command=self._compare_playlist
        )
        compare_btn.pack(side="left",padx=(0, 10))

        ToolTip(
            compare_btn,
            "Compare a local M3U playlist file with a Plex playlist.\n\n"
            "Checks:\n"
            "• Tracks missing in Plex\n"
            "• Extra tracks in Plex\n"
            "• Match percentage\n\n"
            "Use this tool to ensure your local playlist matches the Plex playlist."
        )



    def _build_spotify_tab(self):
        """Build the Spotify integration tab"""
        frame = self.spotify_tab
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # Main info frame
        main_frame = ttk.Frame(frame)
        main_frame.grid(row=0, column=0, sticky="nsew", padx=20, pady=20)
        main_frame.grid_rowconfigure(1, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        # Header
        header_frame = ttk.LabelFrame(
            main_frame, text="🎧 Spotify Integration", padding=20
        )
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 20))

        info_text = """This feature allows you to export your Spotify playlists to M3U format.

To get started:
1. Configure your Spotify API credentials in integrations/spotify.py
2. Set up your client ID and client secret
3. Click the export button below

Note: This feature requires valid Spotify API credentials."""

        info_label = ttk.Label(
            header_frame, text=info_text, wraplength=500, justify="left", font=("", 10)
        )
        info_label.pack(pady=10)

        # Action frame
        action_frame = ttk.Frame(main_frame)
        action_frame.grid(row=1, column=0)

        ttk.Button(
            action_frame,
            text="🎧 Export Spotify Playlists",
            command=self._export_spotify,
        ).pack(pady=20)

        # Status
        self.spotify_status = ttk.Label(
            action_frame,
            text="Configure Spotify credentials first",
            foreground="orange",
        )
        self.spotify_status.pack()

    def _build_tidal_tab(self):
        """Build the Tidal integration tab"""
        frame = self.tidal_tab
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # Main info frame
        main_frame = ttk.Frame(frame)
        main_frame.grid(row=0, column=0, sticky="nsew", padx=20, pady=20)
        main_frame.grid_rowconfigure(1, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        # Header
        header_frame = ttk.LabelFrame(
            main_frame, text="🌊 Tidal Integration", padding=20
        )
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 20))

        info_text = """This feature allows you to export your Tidal playlists to M3U format.

To get started:
1. Configure your Tidal API credentials in integrations/tidal.py
2. Set up your authentication details
3. Click the export button below

Note: This feature requires valid Tidal API credentials."""

        info_label = ttk.Label(
            header_frame, text=info_text, wraplength=500, justify="left", font=("", 10)
        )
        info_label.pack(pady=10)

        # Action frame
        action_frame = ttk.Frame(main_frame)
        action_frame.grid(row=1, column=0)

        ttk.Button(
            action_frame,
            text="🌊 Export Tidal Playlists",
            command=self._export_tidal,
        ).pack(pady=20)

        # Status
        self.tidal_status = ttk.Label(
            action_frame, text="Configure Tidal credentials first", foreground="orange"
        )
        self.tidal_status.pack()

    # === HELPER METHODS ===
    def _log_message(self, message):
        """Add a message to the log text widget"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.update_idletasks()

    def _browse_playlist_folder(self):
        path = filedialog.askdirectory(title="Select Playlist Folder")
        if path:
            self.playlist_folder_var.set(path)
            self._update_status()

    def _browse_local_root(self):
        path = filedialog.askdirectory(title="Select Local Root Directory")
        if path:
            self.playlist_folder_var.set(path)

    def _browse_plex_root(self):
        path = filedialog.askdirectory(title="Select Plex Root Directory")
        if path:
            self.plex_root_var.set(path)

    def _show_path_fix_dialog(self):
        """Show a dialog to help users fix common path issues"""
        dialog = tk.Toplevel(self)
        dialog.title("🔧 Quick Path Fix")
        dialog.geometry("500x350")
        dialog.transient(self)
        dialog.grab_set()
        
        # Main frame
        main_frame = ttk.Frame(dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        
        # Title
        title_label = ttk.Label(main_frame, text="Quick Path Fix", font=("", 14, "bold"))
        title_label.pack(pady=(0, 15))
        
        # Description
        desc_text = """This tool helps you quickly fix common path issues in your playlists.
        
Common problems this fixes:
• Mismatched path separators (\\ vs /)
• Incorrect root paths in playlist files
• URL-encoded spaces (%20) in paths
• Mixed case drive letters (C: vs c:)"""
        
        desc_label = ttk.Label(main_frame, text=desc_text, justify=tk.LEFT, wraplength=450)
        desc_label.pack(pady=(0, 20))
        
        # Path replacement frame
        replace_frame = ttk.LabelFrame(main_frame, text="Path Replacement", padding=10)
        replace_frame.pack(fill=tk.X, pady=(0, 15))
        
        # From path
        ttk.Label(replace_frame, text="Replace this path:").pack(anchor=tk.W)
        self.fix_from_var = tk.StringVar(value="//SERVER/StreamVault/")
        ttk.Entry(replace_frame, textvariable=self.fix_from_var, width=50).pack(fill=tk.X, pady=(2, 10))
        
        # To path
        ttk.Label(replace_frame, text="With this path:").pack(anchor=tk.W)
        self.fix_to_var = tk.StringVar()
        self.fix_to_var.set(self.playlist_folder_var.get() or "D:/Music/")
        ttk.Entry(replace_frame, textvariable=self.fix_to_var, width=50).pack(fill=tk.X, pady=(2, 10))
        
        # Options
        options_frame = ttk.Frame(replace_frame)
        options_frame.pack(fill=tk.X)
        
        self.fix_backup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Create backup files", variable=self.fix_backup_var).pack(side=tk.LEFT)
        
        self.fix_normalize_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Normalize path separators", variable=self.fix_normalize_var).pack(side=tk.LEFT, padx=(20, 0))
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(
            button_frame, 
            text="🔧 Apply Fix", 
            command=lambda: self._apply_path_fix(dialog)
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        ttk.Button(
            button_frame, 
            text="🔍 Preview Changes", 
            command=self._preview_path_fix
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)

    def _apply_path_fix(self, dialog):
        """Apply the path fix to playlist files"""
        from_path = self.fix_from_var.get()
        to_path = self.fix_to_var.get()
        
        if not from_path or not to_path:
            messagebox.showwarning("Warning", "Please enter both 'from' and 'to' paths.")
            return
        
        if not self.generated_files:
            messagebox.showwarning("Warning", "No playlist files selected.")
            return
        
        # Apply the fix
        fixed_count = 0
        for file_path in self.generated_files:
            try:
                # Read file
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Create backup if requested
                if self.fix_backup_var.get():
                    backup_path = file_path + '.backup'
                    with open(backup_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                
                # Apply replacements
                new_content = content.replace(from_path, to_path)
                
                # Normalize path separators if requested
                if self.fix_normalize_var.get():
                    new_content = new_content.replace('\\', '/')
                
                # Write back if changed
                if new_content != content:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    fixed_count += 1
                    
            except Exception as e:
                self._log_message(f"Error fixing {file_path}: {e}")
        
        self._log_message(f"🔧 Path fix complete: {fixed_count} files updated")
        messagebox.showinfo("Success", f"Fixed {fixed_count} files successfully!")
        dialog.destroy()

    def _preview_path_fix(self):
        """Preview what the path fix will do"""
        from_path = self.fix_from_var.get()
        to_path = self.fix_to_var.get()
        
        if not from_path or not to_path:
            messagebox.showwarning("Warning", "Please enter both 'from' and 'to' paths.")
            return
        
        if not self.generated_files:
            messagebox.showwarning("Warning", "No playlist files selected.")
            return
        
        # Show preview
        preview_text = f"Path Fix Preview:\n\n"
        preview_text += f"Replace: {from_path}\n"
        preview_text += f"With: {to_path}\n\n"
        
        files_to_fix = 0
        for file_path in self.generated_files[:3]:  # Preview first 3 files
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                if from_path in content:
                    files_to_fix += 1
                    preview_text += f"✅ {os.path.basename(file_path)}: Will be updated\n"
                else:
                    preview_text += f"⚪ {os.path.basename(file_path)}: No changes needed\n"
                    
            except Exception as e:
                preview_text += f"❌ {os.path.basename(file_path)}: Error reading file\n"
        
        if len(self.generated_files) > 3:
            preview_text += f"... and {len(self.generated_files) - 3} more files\n"
        
        preview_text += f"\nEstimated files to be updated: {files_to_fix}"
        
        messagebox.showinfo("Preview", preview_text)

    def _smart_folder_setup(self):
        """Automatically detect and configure common folder structures"""
        dialog = tk.Toplevel(self)
        dialog.title("🧠 Smart Folder Setup")
        dialog.geometry("600x500")
        dialog.transient(self)
        dialog.grab_set()
        
        # Main frame
        main_frame = ttk.Frame(dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        
        # Title
        title_label = ttk.Label(main_frame, text="Smart Folder Setup", font=("", 14, "bold"))
        title_label.pack(pady=(0, 15))
        
        # Description
        desc_text = """This tool automatically detects common folder structures and configures your paths.
        
It will scan your selected folder and suggest appropriate settings based on what it finds."""
        
        desc_label = ttk.Label(main_frame, text=desc_text, justify=tk.LEFT, wraplength=550)
        desc_label.pack(pady=(0, 20))
        
        # Scan folder selection
        scan_frame = ttk.LabelFrame(main_frame, text="Scan Folder", padding=10)
        scan_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.scan_folder_var = tk.StringVar(value=self.playlist_folder_var.get())
        scan_entry_frame = ttk.Frame(scan_frame)
        scan_entry_frame.pack(fill=tk.X)
        
        ttk.Entry(scan_entry_frame, textvariable=self.scan_folder_var, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(scan_entry_frame, text="Browse", command=self._browse_scan_folder).pack(side=tk.RIGHT, padx=(10, 0))
        
        # Scan button
        ttk.Button(scan_frame, text="🔍 Scan Folder Structure", command=self._scan_folder_structure).pack(pady=(10, 0))
        
        # Results frame
        results_frame = ttk.LabelFrame(main_frame, text="Detection Results", padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        # Results text
        self.results_text = tk.Text(results_frame, height=12, wrap=tk.WORD, font=("Consolas", 9))
        results_scrollbar = ttk.Scrollbar(results_frame, orient="vertical", command=self.results_text.yview)
        self.results_text.config(yscrollcommand=results_scrollbar.set)
        
        self.results_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        results_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(button_frame, text="✅ Apply Suggestions", command=self._apply_suggestions).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)
        
        # Store dialog reference
        self.smart_dialog = dialog
        
        # Auto-scan if folder is already selected
        if self.scan_folder_var.get():
            self._scan_folder_structure()

    def _browse_scan_folder(self):
        """Browse for scan folder"""
        folder = filedialog.askdirectory(title="Select Folder to Scan")
        if folder:
            self.scan_folder_var.set(folder)
            
    def _update_status(self):
        """Update the status display based on current state"""
        count = len(self.generated_files)
        if count > 0:
            self.status_var.set(f"📋 {count} files selected")
        else:
            self.status_var.set("📋 No files selected")

    # === FILE MANAGEMENT METHODS ===
    def _add_m3u8_files(self):
        files = filedialog.askopenfilenames(
            title="Select M3U files",
            filetypes=[("M3U files", "*.m3u"), ("All files", "*.*")],
        )

        new_files = 0
        for file_path in files:
            if file_path not in self.generated_files:
                self.generated_files.append(file_path)
                self.file_listbox.insert(tk.END, os.path.basename(file_path))
                new_files += 1

        if new_files > 0:
            self._log_message(f"Added {new_files} files to selection")
        self._update_status()

    def _add_m3u8_folder(self):
        folder = filedialog.askdirectory(title="Select folder containing M3U files")
        if not folder:
            return

        new_files = []
        for root, dirs, files in os.walk(folder):
            for file in files:
                if file.lower().endswith(".m3u8"):
                    file_path = os.path.join(root, file)
                    if file_path not in self.generated_files:
                        new_files.append(file_path)
                        self.generated_files.append(file_path)
                        self.file_listbox.insert(tk.END, os.path.basename(file_path))

        if new_files:
            self._log_message(f"Added {len(new_files)} files from folder")
        else:
            self._log_message("No new M3U files found in folder")
        self._update_status()

    def _remove_selected_files(self):
        selected_indices = list(self.file_listbox.curselection())
        if not selected_indices:
            messagebox.showwarning("Warning", "Please select files to remove.")
            return

        for index in reversed(selected_indices):
            self.file_listbox.delete(index)
            if index < len(self.generated_files):
                del self.generated_files[index]

        self._log_message(f"Removed {len(selected_indices)} files from selection")
        self._update_status()

    def _clear_all_files(self):
        if not self.generated_files:
            return

        result = messagebox.askquestion("Confirm", "Clear all selected files?")
        if result == "yes":
            self.file_listbox.delete(0, tk.END)
            count = len(self.generated_files)
            self.generated_files.clear()
            self._log_message(f"Cleared {count} files from selection")
            self._update_status()

    def _preview_selected_file(self):
        selected_indices = self.file_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Warning", "Please select a file to preview.")
            return

        file_path = self.generated_files[selected_indices[0]]
        self._show_file_preview(file_path)

    def _show_file_preview(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read(2000)  # Limit preview size

            preview_window = tk.Toplevel(self)
            preview_window.title(f"Preview: {os.path.basename(file_path)}")
            preview_window.geometry("600x400")

            text_widget = tk.Text(preview_window, wrap=tk.WORD, font=("Consolas", 9))
            text_widget.pack(fill="both", expand=True, padx=10, pady=10)
            text_widget.insert(tk.END, content)
            text_widget.config(state=tk.DISABLED)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to preview file: {e}")

    def _auto_discover_files(self):
        root_path = self.playlist_folder_var.get()
        if not root_path:
            messagebox.showerror(
                "Error", "Please select a playlist folder first."
            )
            return

        def task():
            try:
                self._log_message("🔍 Starting auto-discovery of playlist files...")
                
                new_files = []
                for root, dirs, files in os.walk(root_path):
                    for file in files:
                        if file.lower().endswith((".m3u", ".m3u8")):
                            file_path = os.path.join(root, file)
                            if file_path not in self.generated_files:
                                new_files.append(file_path)

                if new_files:
                    self._log_message(f"Found {len(new_files)} new playlist files")
                    result = messagebox.askquestion(
                        "Auto Discovery",
                        f"Found {len(new_files)} new playlist files. Add them to selection?",
                    )
                    if result == "yes":
                        for file_path in new_files:
                            self.generated_files.append(file_path)
                            self.file_listbox.insert(tk.END, os.path.basename(file_path))
                        self._log_message(
                            f"Auto-discovered and added {len(new_files)} files"
                        )
                        self._update_status()
                    else:
                        self._log_message("Auto-discovery cancelled by user")
                else:
                    self._log_message("No new playlist files found")
                    messagebox.showinfo("Auto Discovery", "No new playlist files found.")
            except Exception as e:
                self._log_message(f"Auto discovery failed: {e}")
                messagebox.showerror("Error", f"Auto discovery failed: {e}")

        threading.Thread(target=task, daemon=True).start()

    # === LOG MANAGEMENT ===
    def _save_logs(self):
        content = self.log_text.get(1.0, tk.END).strip()
        if not content:
            messagebox.showwarning("Warning", "No logs to save.")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt")],
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                messagebox.showinfo("Success", "Logs saved successfully.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save logs: {e}")

    def _clear_logs(self):
        self.log_text.delete(1.0, tk.END)

    # === CLEANUP METHODS ===
    def _cleanup_m3u_files(self):
        folder = self.playlist_folder_var.get()
        if not folder:
            messagebox.showerror("Error", "Please select a playlist folder first.")
            return

        try:
            m3u_files = glob.glob(os.path.join(folder, "*.m3u"))
            if not m3u_files:
                messagebox.showinfo("Info", "No M3U files found to clean up.")
                return

            result = messagebox.askquestion(
                "Confirm Cleanup",
                f"Delete {len(m3u_files)} M3U files? This cannot be undone.",
            )
            if result == "yes":
                for file_path in m3u_files:
                    os.remove(file_path)
                self._log_message(f"Deleted {len(m3u_files)} M3U files")
                messagebox.showinfo("Success", f"Deleted {len(m3u_files)} M3U files.")
        except Exception as e:
            messagebox.showerror("Error", f"Cleanup failed: {e}")

    def _cleanup_current_selection(self):
        if not self.generated_files:
            messagebox.showwarning("Warning", "No files in current selection.")
            return

        result = messagebox.askquestion(
            "Confirm Cleanup",
            f"Delete {len(self.generated_files)} selected files? This cannot be undone.",
        )
        if result == "yes":
            deleted = 0
            for file_path in self.generated_files[:]:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        deleted += 1
                except OSError:
                    pass

            self.generated_files.clear()
            self.file_listbox.delete(0, tk.END)
            self._log_message(f"Deleted {deleted} files from selection")
            self._update_status()

    def _refresh_file_lists(self):
        # Remove files that no longer exist
        missing = []
        for i, file_path in enumerate(self.generated_files[:]):
            if not os.path.exists(file_path):
                missing.append(file_path)
                self.generated_files.remove(file_path)

        if missing:
            self._log_message(f"Removed {len(missing)} missing files from selection")
            # Refresh listbox
            self.file_listbox.delete(0, tk.END)
            for file_path in self.generated_files:
                self.file_listbox.insert(tk.END, os.path.basename(file_path))

        self._update_status()

    # === PLACEHOLDER METHODS (to be implemented based on API requirements) ===
    def _run_process_library(self, mode=None):
        folder = self.playlist_folder_var.get()
        if not folder:
            messagebox.showerror("Error", "Please select a playlist folder first.")
            return

        local_root = self.playlist_folder_var.get()
        plex_root = self.plex_root_var.get()

        if not local_root or not plex_root:
            messagebox.showerror(
                "Error", "Please set both Local Root Path and Plex Root Path."
            )
            return

        def task():
            try:
                # Reset progress bar
                self.local_progress_var.set(0)
                
                if mode == "master":
                    self.local_status_var.set("Generating master playlist...")
                    self._log_message("Generating master playlist...")
                    
                    # Get list of M3U files in folder
                    m3u8_files = glob.glob(os.path.join(folder, "*.m3u"))
                    if m3u8_files:
                        self.local_progress_var.set(50)
                        generate_master_playlist(
                            m3u8_files,
                            local_root,
                            plex_root,
                            os.path.join(folder, "master.m3u8"),
                        )
                        self.local_progress_var.set(100)
                        self.local_status_var.set("Master playlist generated successfully!")
                        self.local_stats_var.set(f"Generated master playlist with {len(m3u8_files)} files")
                        self._log_message(
                            f"Master playlist generated with {len(m3u8_files)} files!"
                        )
                    else:
                        self.local_status_var.set("No M3U files found!")
                        self.local_stats_var.set("No files to process")
                        self._log_message("No M3U files found in folder!")
                else:
                    # Count playlist folders for progress tracking
                    playlist_count = 0
                    try:
                        for entry in os.scandir(folder):
                            if entry.is_dir():
                                playlist_count += 1
                    except:
                        playlist_count = 0
                    
                    self.local_status_var.set("Scanning playlist folders...")
                    self.local_stats_var.set(f"Found {playlist_count} playlist folders to process")
                    self._log_message(f"Found {playlist_count} playlist folders to process")
                    
                    # Setup processing parameters
                    encode_spaces = self.encode_spaces_var.get()
                    incremental = self.incremental_var.get()
                    parallel = self.parallel_processing_var.get()
                    
                    # Use .m3u extension for HTTP API
                    ext = ".m3u"

                    self.local_progress_var.set(10)
                    
                    if parallel:
                        self.local_status_var.set("🚀 Using parallel processing...")
                        self._log_message("🚀 Using parallel processing for faster generation...")
                    else:
                        self.local_status_var.set("📝 Using sequential processing...")
                        self._log_message("📝 Using sequential processing...")

                    self.local_progress_var.set(20)
                    
                    # Start processing
                    start_time = time.time()
                    self.local_status_var.set("Processing playlist folders...")
                    
                    generated_files = process_library(
                        folder, local_root, plex_root, encode_spaces, incremental, ext, parallel=parallel
                    )

                    processing_time = time.time() - start_time
                    self.local_progress_var.set(80)

                    if generated_files:
                        self.local_status_var.set("Generating master playlist...")
                        # Generate master playlist with the processed files
                        master_ext = ".m3u" if upload_method == "http" else ".m3u8"
                        generate_master_playlist(
                            generated_files,
                            local_root,
                            plex_root,
                            os.path.join(folder, f"master{master_ext}"),
                        )
                        
                        self.local_progress_var.set(100)
                        self.local_status_var.set("✅ Processing complete!")
                        
                        # Calculate statistics
                        rate = len(generated_files) / processing_time if processing_time > 0 else 0
                        self.local_stats_var.set(
                            f"Generated {len(generated_files)} playlists in {processing_time:.1f}s "
                            f"({rate:.1f} playlists/sec)"
                        )
                        
                        self._log_message(
                            f"✅ Library processing complete! Generated {len(generated_files)} playlists in {processing_time:.1f}s"
                        )
                        
                        # Auto-refresh the file list
                        self.generated_files.clear()
                        self.file_listbox.delete(0, tk.END)
                        
                        for file_path in generated_files:
                            self.generated_files.append(file_path)
                            self.file_listbox.insert(tk.END, os.path.basename(file_path))
                        
                        self._update_status()
                        
                    else:
                        self.local_status_var.set("❌ No playlists generated")
                        self.local_stats_var.set("No playlists were generated")
                        self._log_message("No playlists were generated.")

                messagebox.showinfo("Success", "Processing complete!")
                
            except Exception as e:
                self.local_progress_var.set(0)
                self.local_status_var.set(f"❌ Error: {str(e)}")
                self.local_stats_var.set("Processing failed")
                self._log_message(f"Error: {str(e)}")
                messagebox.showerror("Error", str(e))

        threading.Thread(target=task, daemon=True).start()

    def _fetch_section_id(self):
        if not self.plex_url_var.get() or not self.plex_token_var.get():
            messagebox.showerror("Error", "Please enter Plex URL and token first.")
            return

        # Ask user for library name in main thread
        library_name = simpledialog.askstring(
            "Library Name",
            "Enter the name of your Plex music library:",
            initialvalue="Music",
        )
        if not library_name:
            return

        def task():
            try:
                self._log_message(f"Fetching section ID for library: {library_name}")
                disable_ssl = self.disable_ssl_verification_var.get()
                
                if disable_ssl:
                    self._log_message("🔓 SSL certificate verification disabled")
                    
                section_id = get_section_id_from_library(
                    self.plex_url_var.get(), 
                    self.plex_token_var.get(), 
                    library_name,
                    disable_ssl_verification=disable_ssl
                )
                if section_id:
                    self.section_id_var.set(str(section_id))
                    self._log_message(f"Section ID fetched: {section_id}")
                    messagebox.showinfo("Success", f"Section ID fetched: {section_id}")
                else:
                    self._log_message("Failed to fetch section ID")
                    messagebox.showerror("Error", "Failed to fetch section ID.")
            except Exception as e:
                self._log_message(f"Error fetching section ID: {str(e)}")
                messagebox.showerror("Error", str(e))

        threading.Thread(target=task, daemon=True).start()

    def _test_plex_connection(self):
        if not self.plex_url_var.get() or not self.plex_token_var.get():
            messagebox.showerror("Error", "Please enter Plex URL and token first.")
            return

        def task():
            try:
                self._log_message("Testing Plex connection...")
                disable_ssl = self.disable_ssl_verification_var.get()
                
                if disable_ssl:
                    self._log_message("🔓 SSL certificate verification disabled")
                
                result = test_plex_connection(
                    self.plex_url_var.get(), 
                    self.plex_token_var.get(),
                    disable_ssl_verification=disable_ssl
                )
                if result:
                    self._log_message("✅ Plex connection successful!")
                    messagebox.showinfo("Success", "Connection successful!")
                else:
                    self._log_message("❌ Plex connection failed!")
                    messagebox.showerror(
                        "Error", "Connection failed! Check your URL and token."
                    )
            except Exception as e:
                self._log_message(f"Connection error: {str(e)}")
                messagebox.showerror("Error", str(e))

        threading.Thread(target=task, daemon=True).start()

    def _upload_playlists(self):
        # Check all required parameters
        if not all(
            [
                self.plex_url_var.get(),
                self.plex_token_var.get(),
                self.section_id_var.get(),
            ]
        ):
            messagebox.showerror(
                "Error", "Please fill in all Plex connection details first."
            )
            return

        # Get root paths
        local_root = self.playlist_folder_var.get()
        plex_root = self.plex_root_var.get()

        if not local_root or not plex_root:
            messagebox.showerror(
                "Error", "Please set both Local Root Path and Plex Root Path."
            )
            return

        # Get playlist files from the file listbox
        files = self.generated_files

        # If no files selected, try to auto-discover from playlist folder
        if not files:
            folder = self.playlist_folder_var.get()
            if folder and os.path.exists(folder):
                files = glob.glob(os.path.join(folder, "*.m3u"))
                files.extend(glob.glob(os.path.join(folder, "*.m3u")))
                
        if not files:
            messagebox.showerror("Error", "No playlist files found. Please add files to the playlist list or select a playlist folder.")
            return

        # Check path compatibility
        is_compatible, suggestions, mismatches = self._check_path_compatibility(
            files, local_root
        )

        if not is_compatible:
            # Show warning and ask how to proceed
            proceed = self._show_path_mismatch_warning(mismatches, suggestions)
            if not proceed:
                return  # User chose to cancel or fix paths

        # Check playlist content
        valid_files, empty_files, invalid_files = self._check_playlist_content(files)
        
        if empty_files or invalid_files:
            proceed = self._show_empty_playlist_warning(empty_files, invalid_files)
            if not proceed:
                return  # User chose to cancel due to playlist issues
        
        # Use only valid files for upload
        files = valid_files

        self.progress_var.set(0)
        self.status_var.set("Starting upload...")

        def task():
            try:
                total_files = len(files)
                encode_spaces = self.encode_spaces_var.get()
                successful_uploads = 0
                failed_uploads = []

                for i, playlist_file in enumerate(files):
                    playlist_name = os.path.splitext(os.path.basename(playlist_file))[0]
                    self.status_var.set(
                        f"Uploading: {playlist_name} ({i+1}/{total_files})"
                    )
                    self.update_idletasks()

                    self._log_message(f"📤 Uploading playlist: {playlist_name}")

                    try:
                        # Use HTTP API method to reference existing server files
                        self._log_message(
                            f"📡 Using HTTP API method (references existing server files)"
                        )
                        
                        # Convert local path to server path
                        # From: //192.168.178.11/StreamVault/AudioVault/Musik/Playlists/chilly shit/chilly shit.m3u8
                        # To:   /media/music/Playlists/chilly shit/chilly shit.m3u
                        server_file_path = playlist_file.replace(local_root, plex_root).replace('\\', '/')
                        # Change extension from .m3u8 to .m3u as Plex expects .m3u files
                        if server_file_path.endswith('.m3u8'):
                            server_file_path = server_file_path[:-5] + '.m3u'
                        
                        # Get SSL setting
                        disable_ssl = self.disable_ssl_verification_var.get()
                        
                        upload_playlist_via_http_api(
                            playlist_file,
                            local_root,
                            plex_root,
                            server_file_path,  # Pass the server file path
                            self.section_id_var.get(),
                            self.plex_token_var.get(),
                            self.plex_url_var.get(),
                            encode_spaces,
                            disable_ssl,  # Pass SSL setting
                        )
                        self._log_message(
                            f"✅ Successfully uploaded via HTTP API: {playlist_name}"
                        )
                        successful_uploads += 1

                    except Exception as e:
                        error_msg = f"❌ Error uploading {playlist_name}: {str(e)}"
                        self._log_message(error_msg)
                        failed_uploads.append(f"{playlist_name} ({str(e)[:50]}...)")
                        # Continue with other playlists

                    # Update progress
                    progress = ((i + 1) / total_files) * 100
                    self.progress_var.set(progress)
                    self.update_idletasks()

                # Show detailed completion summary
                self.status_var.set("Upload complete!")
                
                if failed_uploads:
                    summary_msg = f"Upload completed with mixed results:\n\n"
                    summary_msg += f"✅ Successful uploads: {successful_uploads}/{total_files}\n"
                    summary_msg += f"❌ Failed uploads: {len(failed_uploads)}/{total_files}\n\n"
                    
                    if len(failed_uploads) <= 5:
                        summary_msg += "Failed playlists:\n"
                        for failed in failed_uploads:
                            summary_msg += f"• {failed}\n"
                    else:
                        summary_msg += f"Failed playlists (first 5):\n"
                        for failed in failed_uploads[:5]:
                            summary_msg += f"• {failed}\n"
                        summary_msg += f"... and {len(failed_uploads) - 5} more\n"
                    
                    summary_msg += "\nCheck the console log for detailed error information."
                    
                    self._log_message(f"📊 Upload summary: {successful_uploads} successful, {len(failed_uploads)} failed")
                    messagebox.showwarning("Upload Complete with Errors", summary_msg)
                else:
                    self._log_message(f"✅ Successfully uploaded all {total_files} playlists!")
                    messagebox.showinfo(
                        "Success", f"All {total_files} playlists uploaded successfully!"
                    )
            except Exception as e:
                self.status_var.set("Upload failed!")
                self._log_message(f"❌ Upload error: {str(e)}")
                messagebox.showerror("Error", str(e))

        threading.Thread(target=task, daemon=True).start()

    def _verify_uploads(self):
        if not all(
            [
                self.plex_url_var.get(),
                self.plex_token_var.get(),
            ]
        ):
            messagebox.showerror("Error", "Please enter Plex URL and token first.")
            return

        folder = self.playlist_folder_var.get()
        if not folder:
            messagebox.showerror("Error", "Please select a playlist folder first.")
            return

        def task():
            try:
                self._log_message("Verifying uploaded playlists...")
                disable_ssl = self.disable_ssl_verification_var.get()
                
                if disable_ssl:
                    self._log_message("🔓 SSL certificate verification disabled")
                
                # Get expected playlists from folder
                expected_playlists = {
                    os.path.splitext(os.path.basename(f))[0]
                    for f in glob.glob(os.path.join(folder, "*.m3u"))
                }

                if not expected_playlists:
                    self._log_message("⚠️ No M3U files found in playlist folder")
                    messagebox.showwarning("Warning", "No M3U files found in the playlist folder.")
                    return

                self._log_message(f"Found {len(expected_playlists)} playlists to verify")

                verify_uploaded_playlists(
                    self.plex_url_var.get(),
                    self.plex_token_var.get(),
                    expected_playlists,
                )
                self._log_message("✅ Verification complete!")
                messagebox.showinfo(
                    "Complete", "Verification complete. Check console output."
                )
            except Exception as e:
                self._log_message(f"❌ Verification error: {str(e)}")
                messagebox.showerror("Error", str(e))

        threading.Thread(target=task, daemon=True).start()

    def _verify_content(self):
        if not all(
            [
                self.plex_url_var.get(),
                self.plex_token_var.get(),
            ]
        ):
            messagebox.showerror("Error", "Please enter Plex URL and token first.")
            return

        folder = self.playlist_folder_var.get()
        if not folder:
            messagebox.showerror("Error", "Please select a playlist folder first.")
            return

        def task():
            try:
                self._log_message("Verifying playlist content...")
                disable_ssl = self.disable_ssl_verification_var.get()
                
                if disable_ssl:
                    self._log_message("🔓 SSL certificate verification disabled")
                
                # Get generated files from folder
                generated_files = glob.glob(os.path.join(folder, "*.m3u"))

                if not generated_files:
                    self._log_message("⚠️ No M3U files found in playlist folder")
                    messagebox.showwarning("Warning", "No M3U files found in the playlist folder.")
                    return

                self._log_message(f"Found {len(generated_files)} playlist files to verify")

                verify_local_playlists_content_in_plex(
                    self.plex_url_var.get(), 
                    self.plex_token_var.get(), 
                    generated_files,
                    disable_ssl_verification=disable_ssl
                )
                self._log_message("✅ Content verification complete!")
                messagebox.showinfo(
                    "Complete", "Content verification complete. Check console output."
                )
            except Exception as e:
                self._log_message(f"❌ Content verification error: {str(e)}")
                messagebox.showerror("Error", str(e))

        threading.Thread(target=task, daemon=True).start()

    def _compare_playlist(self):
        if not all(
            [
                self.plex_url_var.get(),
                self.plex_token_var.get(),
                self.section_id_var.get(),
            ]
        ):
            messagebox.showerror(
                "Error", "Please fill in all Plex connection details first."
            )
            return

        # Ask user to select M3U file
        m3u8_file = filedialog.askopenfilename(
            title="Select M3U file to compare",
            filetypes=[("M3U files", "*.m3u"), ("All files", "*.*")],
        )

        if not m3u8_file:
            return

        # Ask for playlist name
        playlist_name = simpledialog.askstring(
            "Playlist Name", "Enter the name of the Plex playlist to compare with:"
        )

        if not playlist_name:
            return

        def task():
            try:
                self._log_message(f"Comparing playlist: {playlist_name}")
                disable_ssl = self.disable_ssl_verification_var.get()
                
                if disable_ssl:
                    self._log_message("🔓 SSL certificate verification disabled")
                
                results = compare_m3u8_to_plex_playlist(
                    m3u8_file,
                    self.plex_url_var.get(),
                    self.plex_token_var.get(),
                    int(self.section_id_var.get()),
                    playlist_name,
                    disable_ssl_verification=disable_ssl
                )

                # Format results for display
                missing_count = len(results.get("missing_in_plex", []))
                extra_count = len(results.get("extra_in_plex", []))
                total_m3u8 = results.get("total_m3u8_tracks", 0)
                total_plex = results.get("total_plex_tracks", 0)
                match_percentage = results.get("match_percentage", 0)

                message = f"Comparison Results for '{playlist_name}':\n\n"
                message += f"M3U tracks: {total_m3u8}\n"
                message += f"Plex tracks: {total_plex}\n"
                message += f"Match percentage: {match_percentage}%\n\n"

                if missing_count == 0 and extra_count == 0:
                    message += "✅ Perfect match! All tracks are synchronized."
                else:
                    message += f"Missing in Plex: {missing_count} tracks\n"
                    message += f"Extra in Plex: {extra_count} tracks\n\n"
                    message += "Check the console output for detailed track lists."

                self._log_message(f"Comparison complete: {match_percentage}% match")
                messagebox.showinfo("Comparison Results", message)

            except Exception as e:
                self._log_message(f"❌ Comparison error: {str(e)}")
                messagebox.showerror(
                    "Comparison Error", f"Error comparing playlist: {e}"
                )

        threading.Thread(target=task, daemon=True).start()

    def _export_spotify(self):
        self._log_message("Spotify integration not implemented yet")
        messagebox.showinfo(
            "Info",
            "Spotify integration is not fully implemented yet.\n"
            "Please configure the Spotify integration in integrations/spotify.py first.",
        )

    def _export_tidal(self):
        self._log_message("Tidal integration not implemented yet")
        messagebox.showinfo(
            "Info",
            "Tidal integration is not fully implemented yet.\n"
            "Please configure the Tidal integration in integrations/tidal.py first.",
        )

    def _check_path_compatibility(self, playlist_files, local_root):
        """
        Check if the local root path is compatible with the paths in playlist files.
        Returns a tuple: (is_compatible, suggestions, mismatches)
        """
        if not playlist_files or not local_root:
            return True, [], []

        # Sample the first few playlist files to check paths
        sample_files = playlist_files[:3]  # Check first 3 files
        path_mismatches = []
        suggested_roots = set()

        for playlist_file in sample_files:
            try:
                with open(playlist_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                # Extract first few actual file paths (not comments or empty lines)
                file_paths = []
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#') and os.path.sep in line:
                        file_paths.append(line)
                        if len(file_paths) >= 3:  # Sample first 3 paths
                            break

                # Check if any paths start with the local root
                for file_path in file_paths:
                    normalized_file_path = os.path.normpath(file_path).replace('\\', '/')
                    normalized_local_root = os.path.normpath(local_root).replace('\\', '/')
                    
                    if not normalized_file_path.startswith(normalized_local_root):
                        path_mismatches.append({
                            'playlist': os.path.basename(playlist_file),
                            'file_path': file_path,
                            'local_root': local_root
                        })
                        
                        # Try to suggest a better root path
                        if '/StreamVault/' in file_path or '\\StreamVault\\' in file_path:
                            if file_path.startswith('/mnt/MediaSphere/'):
                                suggested_roots.add('/mnt/MediaSphere/')
                            elif file_path.startswith('//TRUENAS/'):
                                suggested_roots.add('//TRUENAS/')

            except Exception as e:
                self._log_message(f"Warning: Could not check paths in {playlist_file}: {e}")
                continue

        is_compatible = len(path_mismatches) == 0
        suggestions = list(suggested_roots)
        
        return is_compatible, suggestions, path_mismatches

    def _show_path_mismatch_warning(self, mismatches, suggestions):
        """Show a warning dialog for path mismatches with suggestions."""
        if not mismatches:
            return True

        # Create a detailed warning message
        warning_msg = "⚠️ Path Mismatch Detected!\n\n"
        warning_msg += "Your Local Root Path doesn't match the paths in your playlist files.\n"
        warning_msg += "This will result in no tracks being uploaded.\n\n"
        
        warning_msg += "Examples of mismatched paths:\n"
        for i, mismatch in enumerate(mismatches[:3]):  # Show first 3 examples
            warning_msg += f"• Playlist: {mismatch['playlist']}\n"
            warning_msg += f"  File path: {mismatch['file_path'][:80]}...\n"
            warning_msg += f"  Local root: {mismatch['local_root']}\n\n"
        
        if suggestions:
            warning_msg += "Suggested Local Root Paths:\n"
            for suggestion in suggestions[:3]:  # Show first 3 suggestions
                warning_msg += f"• {suggestion}\n"
            warning_msg += "\n"
        
        warning_msg += "Please update your Local Root Path to match your playlist file paths,\n"
        warning_msg += "or use the Path Remapping Tool to fix your playlist files."
        
        # Create a custom dialog with options
        result = messagebox.askyesnocancel(
            "Path Mismatch Warning",
            warning_msg + "\n\nOptions:\n"
            "• Yes: Continue anyway (may upload 0 tracks)\n"
            "• No: Cancel upload to fix paths\n"
            "• Cancel: Open Path Remapping Tool"
        )
        
        if result is None:  # Cancel was clicked - open path remapping tool
            self._open_path_remapping_tool(mismatches, suggestions)
            return False
        elif result is False:  # No was clicked - cancel upload
            return False
        else:  # Yes was clicked - continue anyway
            self._log_message("⚠️ User chose to continue despite path mismatch - expect 0 tracks uploaded")
            return True

    def _open_path_remapping_tool(self, mismatches, suggestions):
        """Open a simplified dialog to help users remap paths."""
        remap_window = tk.Toplevel(self)
        remap_window.title("Path Remapping Tool")
        remap_window.geometry("600x400")
        remap_window.transient(self)
        remap_window.grab_set()
        
        # Main frame
        main_frame = ttk.Frame(remap_window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Title
        title_label = ttk.Label(main_frame, text="Fix Path Mismatch", font=("TkDefaultFont", 14, "bold"))
        title_label.pack(pady=(0, 10))
        
        # Info
        info_text = "Choose one of these options to fix the path mismatch:\n\n"
        info_text += "Option 1: Update your Local Root Path\n"
        info_text += "Option 2: Rewrite playlist files to match your current Local Root Path"
        
        info_label = ttk.Label(main_frame, text=info_text, justify=tk.LEFT)
        info_label.pack(anchor=tk.W, pady=(0, 15))
        
        # Current paths
        current_frame = ttk.LabelFrame(main_frame, text="Current Settings", padding=10)
        current_frame.pack(fill=tk.X, pady=(0, 15))
        
        ttk.Label(current_frame, text=f"Local Root: {self.playlist_folder_var.get()}", foreground="blue").pack(anchor=tk.W)
        if mismatches:
            example_path = mismatches[0]['file_path'][:60] + "..." if len(mismatches[0]['file_path']) > 60 else mismatches[0]['file_path']
            ttk.Label(current_frame, text=f"Playlist paths start with: {example_path}", foreground="red").pack(anchor=tk.W)
        
        # Option 1: Suggested roots
        if suggestions:
            option1_frame = ttk.LabelFrame(main_frame, text="Option 1: Use Suggested Local Root", padding=10)
            option1_frame.pack(fill=tk.X, pady=(0, 10))
            
            for suggestion in suggestions[:2]:  # Show first 2 suggestions
                suggestion_frame = ttk.Frame(option1_frame)
                suggestion_frame.pack(fill=tk.X, pady=2)
                
                ttk.Label(suggestion_frame, text=suggestion, foreground="green").pack(side=tk.LEFT)
                ttk.Button(
                    suggestion_frame, 
                    text="Use This", 
                    command=lambda s=suggestion: self._apply_suggested_root(s, remap_window)
                ).pack(side=tk.RIGHT)
        
        # Option 2: Path rewriting
        option2_frame = ttk.LabelFrame(main_frame, text="Option 2: Rewrite Playlist Files", padding=10)
        option2_frame.pack(fill=tk.X, pady=(0, 15))
        
        # Pre-fill common replacements
        find_text = ""
        replace_text = self.playlist_folder_var.get()
        
        if suggestions and mismatches:
            first_path = mismatches[0]['file_path']
            if '/mnt/MediaSphere/' in first_path:
                find_text = '/mnt/MediaSphere/'
            elif '//TRUENAS/' in first_path:
                find_text = '//TRUENAS/'
        
        rewrite_frame = ttk.Frame(option2_frame)
        rewrite_frame.pack(fill=tk.X)
        
        ttk.Label(rewrite_frame, text="Replace:").grid(row=0, column=0, sticky=tk.W, pady=2)
        find_var = tk.StringVar(value=find_text)
        ttk.Entry(rewrite_frame, textvariable=find_var, width=40).grid(row=0, column=1, padx=(5, 0), pady=2)
        
        ttk.Label(rewrite_frame, text="With:").grid(row=1, column=0, sticky=tk.W, pady=2)
        replace_var = tk.StringVar(value=replace_text)
        ttk.Entry(rewrite_frame, textvariable=replace_var, width=40).grid(row=1, column=1, padx=(5, 0), pady=2)
        
        ttk.Button(
            option2_frame, 
            text="Apply Path Changes (creates backups)", 
            command=lambda: self._apply_path_changes_simple(find_var.get(), replace_var.get(), remap_window)
        ).pack(pady=(10, 0))
        
        # Close button
        ttk.Button(main_frame, text="Close", command=remap_window.destroy).pack(pady=(10, 0))

    def _apply_suggested_root(self, suggestion, window):
        """Apply a suggested root path and close the tool."""
        self.playlist_folder_var.set(suggestion)
        self._log_message(f"✅ Updated Local Root Path to: {suggestion}")
        messagebox.showinfo("Success", f"Local Root Path updated to:\n{suggestion}")
        window.destroy()

    def _apply_path_changes_simple(self, find_text, replace_text, remap_window):
        """Apply path changes with simplified interface."""
        if not find_text or not replace_text:
            messagebox.showwarning("Warning", "Please enter both find and replace text.")
            return
        
        if not messagebox.askyesno("Confirm Changes", 
                                   f"This will modify your playlist files and create backups.\n\n"
                                   f"Replace: {find_text}\n"
                                   f"With: {replace_text}\n\n"
                                   f"Continue?"):
            return
        
        # Get playlist files
        if self.mode_var.get() == "generate":
            folder = self.playlist_folder_var.get()
            if not folder:
                messagebox.showwarning("Warning", "Please select a playlist folder first.")
                return
            files = glob.glob(os.path.join(folder, "*.m3u"))
        else:
            files = self.generated_files
        
        if not files:
            messagebox.showwarning("Warning", "No playlist files found.")
            return
        
        modified_files = 0
        
        for playlist_file in files:
            try:
                # Create backup
                backup_file = playlist_file + '.backup.' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                shutil.copy2(playlist_file, backup_file)
                
                # Read and modify file
                with open(playlist_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                new_content = content.replace(find_text, replace_text)
                
                if new_content != content:
                    with open(playlist_file, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    modified_files += 1
                    self._log_message(f"✅ Modified paths in: {os.path.basename(playlist_file)}")
                
            except Exception as e:
                self._log_message(f"❌ Error modifying {os.path.basename(playlist_file)}: {e}")
        
        messagebox.showinfo("Complete", 
                           f"Path remapping complete!\n\n"
                           f"Modified {modified_files} files.\n"
                           f"Backup files created with .backup.[timestamp] extension.")
        
        self._log_message(f"✅ Path remapping complete: {modified_files} files modified")
        remap_window.destroy()

    def _check_playlist_content(self, playlist_files):
        """
        Check if playlist files contain actual tracks.
        Returns (valid_files, empty_files, invalid_files)
        """
        valid_files = []
        empty_files = []
        invalid_files = []
        
        for playlist_file in playlist_files:
            try:
                with open(playlist_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                # Count actual track lines (not comments or empty lines)
                track_count = 0
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#') and (os.path.sep in line or '/' in line):
                        track_count += 1
                
                if track_count == 0:
                    empty_files.append(playlist_file)
                else:
                    valid_files.append(playlist_file)
                    
            except Exception as e:
                self._log_message(f"Warning: Could not read {os.path.basename(playlist_file)}: {e}")
                invalid_files.append(playlist_file)
        
        return valid_files, empty_files, invalid_files

    def _show_empty_playlist_warning(self, empty_files, invalid_files):
        """Show warning about empty or invalid playlist files."""
        if not empty_files and not invalid_files:
            return True
        
        warning_msg = "⚠️ Playlist Issues Detected!\n\n"
        
        if empty_files:
            warning_msg += f"Empty playlists (no tracks found): {len(empty_files)}\n"
            for empty_file in empty_files[:3]:  # Show first 3
                warning_msg += f"• {os.path.basename(empty_file)}\n"
            if len(empty_files) > 3:
                warning_msg += f"... and {len(empty_files) - 3} more\n"
            warning_msg += "\n"
        
        if invalid_files:
            warning_msg += f"Unreadable playlists: {len(invalid_files)}\n"
            for invalid_file in invalid_files[:3]:  # Show first 3
                warning_msg += f"• {os.path.basename(invalid_file)}\n"
            if len(invalid_files) > 3:
                warning_msg += f"... and {len(invalid_files) - 3} more\n"
            warning_msg += "\n"
        
        warning_msg += "These playlists may not upload successfully.\n\n"
        warning_msg += "Do you want to continue with the upload anyway?"
        
        result = messagebox.askyesno("Playlist Issues Warning", warning_msg)
        
        if result:
            self._log_message("⚠️ User chose to continue despite playlist issues")
            return True
        else:
            self._log_message("Upload cancelled due to playlist issues")
            return False
        
    def _quick_fix_common_issues(self):
        """Automatically detect and fix common playlist issues."""
        # Get playlist files from the file listbox
        files = self.generated_files

        # If no files selected, try to auto-discover from playlist folder
        if not files:
            folder = self.playlist_folder_var.get()
            if folder and os.path.exists(folder):
                files = glob.glob(os.path.join(folder, "*.m3u"))
                files.extend(glob.glob(os.path.join(folder, "*.m3u")))
                
        if not files:
            messagebox.showwarning("Warning", "No playlist files found. Please add files to the playlist list or select a playlist folder.")
            return

        if not messagebox.askyesno("Quick Fix", 
                                   f"This will analyze and fix common issues in {len(files)} playlist files.\n\n"
                                   f"Issues that will be fixed:\n"
                                   f"• Remove empty playlists\n"
                                   f"• Remove duplicate track entries\n"
                                   f"• Fix common path encoding issues\n"
                                   f"• Remove invalid/non-existent file paths\n\n"
                                   f"Backup files will be created. Continue?"):
            return

        self._log_message("🔧 Starting quick fix for common playlist issues...")
        
        fixed_files = 0
        issues_found = 0
        
        for playlist_file in files:
            try:
                # Read the playlist
                with open(playlist_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                original_lines = lines[:]
                modified = False
                
                # Track unique entries to remove duplicates
                seen_tracks = set()
                cleaned_lines = []
                tracks_found = 0
                duplicates_removed = 0
                invalid_paths_removed = 0
                
                for line in lines:
                    line_stripped = line.strip()
                    
                    # Keep comments and header lines
                    if line_stripped.startswith('#') or not line_stripped:
                        cleaned_lines.append(line)
                        continue
                    
                    # Check if it's a file path
                    if '/' in line_stripped or '\\' in line_stripped:
                        tracks_found += 1
                        
                        # Remove duplicates
                        normalized_path = os.path.normpath(line_stripped.lower())
                        if normalized_path in seen_tracks:
                            duplicates_removed += 1
                            modified = True
                            self._log_message(f"  Removed duplicate: {os.path.basename(line_stripped)}")
                            continue
                        
                        seen_tracks.add(normalized_path)
                        
                        # Fix common encoding issues
                        fixed_line = line_stripped
                        if '%20' in fixed_line and ' ' not in fixed_line:
                            # URL-decode the path
                            import urllib.parse
                            try:
                                decoded = urllib.parse.unquote(fixed_line)
                                if os.path.exists(decoded) or '/mnt/' in decoded or '//TRUENAS' in decoded:
                                    fixed_line = decoded
                                    modified = True
                                    self._log_message(f"  Fixed encoding: {os.path.basename(fixed_line)}")
                            except:
                                pass
                        
                        cleaned_lines.append(fixed_line + '\n')
                    else:
                        # Keep other lines as-is
                        cleaned_lines.append(line)
                
                # Skip empty playlists
                if tracks_found == 0:
                    self._log_message(f"  Skipping empty playlist: {os.path.basename(playlist_file)}")
                    continue
                
                # Write the cleaned playlist if modified
                if modified or duplicates_removed > 0:
                    # Create backup
                    backup_file = playlist_file + '.backup.' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                    shutil.copy2(playlist_file, backup_file)
                    
                    # Write cleaned version
                    with open(playlist_file, 'w', encoding='utf-8') as f:
                        f.writelines(cleaned_lines)
                    
                    fixed_files += 1
                    issues_found += duplicates_removed + invalid_paths_removed
                    
                    self._log_message(f"✅ Fixed {os.path.basename(playlist_file)}: "
                                    f"{duplicates_removed} duplicates removed, {tracks_found} tracks remaining")
                
            except Exception as e:
                self._log_message(f"❌ Error processing {os.path.basename(playlist_file)}: {e}")
        
        completion_msg = f"🔧 Quick Fix Complete!\n\n"
        completion_msg += f"Files processed: {len(files)}\n"
        completion_msg += f"Files fixed: {fixed_files}\n"
        completion_msg += f"Issues resolved: {issues_found}\n\n"
        
        if fixed_files > 0:
            completion_msg += f"Backup files created with .backup.[timestamp] extension."
        else:
            completion_msg += f"No issues found - all playlists are clean!"
        
        messagebox.showinfo("Quick Fix Complete", completion_msg)
        self._log_message(f"🔧 Quick fix complete: {fixed_files} files fixed, {issues_found} issues resolved")

    # === CONFIG MANAGEMENT METHODS ===
    def _save_plex_config(self):
        """Save current Plex settings to encrypted config file"""
        try:
            config_data = {
                'plex_url': self.plex_url_var.get(),
                'plex_token': self.plex_token_var.get(),
                'section_id': self.section_id_var.get(),
                'disable_ssl_verification': self.disable_ssl_verification_var.get(),
                'playlist_folder': self.playlist_folder_var.get(),
                'plex_root': self.plex_root_var.get(),
                'encode_spaces': self.encode_spaces_var.get(),
                'incremental': self.incremental_var.get(),
                'parallel_processing': self.parallel_processing_var.get(),
            }
            
            if self.config_manager.save_config(config_data):
                messagebox.showinfo("Success", "Plex settings saved successfully!\n\nSettings are encrypted and stored securely.")
                self._log_message("💾 Plex settings saved to encrypted config file")
            else:
                messagebox.showerror("Error", "Failed to save Plex settings.")
                self._log_message("❌ Failed to save Plex settings")
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings: {e}")
            self._log_message(f"❌ Error saving settings: {e}")

    def _load_plex_config(self):
        """Load Plex settings from encrypted config file"""
        try:
            if not self.config_manager.config_exists():
                return
                
            config_data = self.config_manager.load_config()
            
            if not config_data:
                return
                
            # Load connection settings
            if 'plex_url' in config_data:
                self.plex_url_var.set(config_data['plex_url'])
            if 'plex_token' in config_data:
                self.plex_token_var.set(config_data['plex_token'])
            if 'section_id' in config_data:
                self.section_id_var.set(config_data['section_id'])
            if 'disable_ssl_verification' in config_data:
                self.disable_ssl_verification_var.set(config_data['disable_ssl_verification'])
                
            # Load other settings
            if 'playlist_folder' in config_data:
                self.playlist_folder_var.set(config_data['playlist_folder'])
            if 'plex_root' in config_data:
                self.plex_root_var.set(config_data['plex_root'])
            if 'encode_spaces' in config_data:
                self.encode_spaces_var.set(config_data['encode_spaces'])
            if 'incremental' in config_data:
                self.incremental_var.set(config_data['incremental'])
            if 'parallel_processing' in config_data:
                self.parallel_processing_var.set(config_data['parallel_processing'])
                
            self._log_message("📂 Plex settings loaded from encrypted config file")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load settings: {e}")
            self._log_message(f"❌ Error loading settings: {e}")

    def _clear_plex_config(self):
        """Clear saved Plex settings"""
        try:
            result = messagebox.askquestion(
                "Confirm Clear Settings",
                "Are you sure you want to delete all saved Plex settings?\n\nThis action cannot be undone."
            )
            
            if result == "yes":
                if self.config_manager.delete_config():
                    # Clear current form values
                    self.plex_url_var.set("")
                    self.plex_token_var.set("")
                    self.section_id_var.set("")
                    self.disable_ssl_verification_var.set(False)
                    
                    messagebox.showinfo("Success", "Plex settings cleared successfully!")
                    self._log_message("🗑️ Plex settings cleared and config file deleted")
                else:
                    messagebox.showerror("Error", "Failed to clear Plex settings.")
                    self._log_message("❌ Failed to clear Plex settings")
                    
        except Exception as e:
            messagebox.showerror("Error", f"Failed to clear settings: {e}")
            self._log_message(f"❌ Error clearing settings: {e}")


if __name__ == "__main__":
    app = PlexSyncerGUI()
    app.mainloop()