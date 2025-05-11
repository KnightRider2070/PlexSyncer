# PlexSyncer

**PlexSyncer** is a powerful command-line tool written in Python that automates the creation, remapping, management, and
uploading of m3u8 playlists for Plex. It scans a specified folder containing media files (organized in subdirectories),
generates m3u8 playlists with proper headers and track metadata, and optionally uploads these playlists to your Plex
server via its API.

With support for incremental updates, the use of existing playlists, and content verification via the Plex API,
PlexSyncer offers a convenient solution for managing your Plex media library.

## Features

### 1. **Automated Playlist Generation**

- PlexSyncer scans a root folder of media files (organized in subdirectories) and generates m3u8 playlists with
  `#EXTM3U` headers and `#EXTINF` lines, which include track duration and title metadata.

### 2. **Path Remapping**

- Use two path-mapping parameters: `--m3u8-local-root` (your local storage location) and `--m3u8-plex-root` (the
  corresponding Plex path). This allows PlexSyncer to automatically convert local file paths to Plex-friendly URLs.
    - Example:

      ```plaintext
      \\SERVERIP\PATH\TO\MEDIA\Playlists\FOLDER\PALYLISTFILE.m3u8
      ```

      becomes

      ```plaintext
      /LOCAL/SERVER/Playlists/PATH/PALYLISTFILE.m3u8
      ```

### 3. **Incremental Updates**

- PlexSyncer supports incremental updates, meaning only new tracks are appended to existing playlists instead of
  regenerating the whole file. This helps avoid redundant operations.

### 4. **Use Existing Playlists**

- If desired, PlexSyncer can use existing m3u8 files from the specified folder, skipping regeneration to save time.

### 5. **Automatic Plex Section Lookup**

- PlexSyncer automatically fetches the corresponding section ID from Plex using the provided `--plex-url`,
  `--plex-token`, and `--library-name`. This eliminates the need to manually provide the section ID.

### 6. **Upload Playlists to Plex**

- Once generated, playlists can be uploaded to Plex via a configurable API endpoint. PlexSyncer automatically renames
  `.m3u8` files to `.m3u` before uploading them.

### 7. **Verification**

- **Plex Upload Verification**: Checks (by folder name) whether the playlists have been successfully uploaded to Plex.
- **Local Content Verification**: Compares the track titles in each local m3u8 file with the items in the corresponding
  Plex playlist to ensure consistency.

### 8. **Master Playlist Generation**

- PlexSyncer generates a master m3u8 file (`master.m3u8`) that lists all remapped playlist paths, providing a
  centralized entry point for accessing all individual playlists.

### 9. **Spotify Integration**

- PlexSyncer can fetch Spotify playlists using the Spotify Web API and generate corresponding m3u8 files for integration
  with Plex. This feature supports both client credentials and user OAuth authentication.
  It is currently in development and will be available in future releases.
- [ ] Use PlexApi to search for tracks from Spotify then create a playlist in Plex and add the tracks to it.

## Requirements

- **Python 3.x**
- **Dependencies:**
    - `requests`
    - `mutagen` (for extracting track metadata)
    - `plexapi` (for Plex API interactions)
    - `spotipy` (for Spotify integration)
- **Supported OS:** Windows, macOS, or Linux

## Installation

1. **Clone or Download PlexSyncer:**

   ```bash
   git clone https://github.com/KnightRider2070/PlexSyncer.git
   cd PlexSyncer
   ```

2. **Install Required Packages:**

   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Essential Arguments

- `--playlist-folder`:  
  The folder that contains subdirectories for each playlist.

- `--m3u8-local-root`:  
  The local root folder for m3u8 files (e.g., `\\SERVERIP\PATH\TO\MEDIA\Playlists`).

- `--m3u8-plex-root`:  
  The corresponding Plex root folder (e.g., `/media/music/Playlists`).

- `--library-name`:  
  The name of the target Plex library (e.g., `"Musik"`). PlexSyncer will automatically fetch the corresponding section
  ID.

- `--plex-url`:  
  The base URL of your Plex server (e.g., `http://localhost:32400`).

- `--plex-token`:  
  Your Plex authentication token.

### Optional Arguments

- `--api-url`:  
  The API endpoint URL for uploading playlists. (Default: `https://plex.example.com/playlists/upload`)

- `--incremental`:  
  If provided, only new tracks are appended to an existing playlist.

- `--generate-only`:  
  If provided, PlexSyncer generates or updates playlists only and skips uploading them.

- `--use-existing`:  
  If provided, the tool uses the existing m3u8 files in the playlist folder without regenerating them.

- `--verify-uploads`:  
  Verifies via the Plex API that all local playlists (by folder name) exist on Plex.

- `--verify-m3u8`:  
  Checks that each local m3u8 file’s track titles match those in the corresponding Plex playlist.

- `--encode-spaces`:  
  Encodes spaces in file paths as `%20` for URL safety.

- `--verbose`:  
  Enables verbose (DEBUG-level) logging.

### Example Commands

#### Generate, Upload, and Verify Playlists

```bash
plexsyncer --playlist-folder "\\SERVERIP\\PATH\\TO\\MEDIA\\Playlists" \
  --m3u8-local-root "\\SERVERIP\\PATH\\TO\\MEDIA\\Playlists" \
  --m3u8-plex-root "/media/music/Playlists" \
  --library-name "Musik" --plex-url "http://localhost:32400" --plex-token "your_token" \
  --api-url "https://plex.example.com/playlists/upload" \
  --incremental --verify-uploads --verify-m3u8 --verbose
```

#### Use Only Existing Playlists and Verify Content

```bash
plexsyncer --playlist-folder "\\SERVERIP\\PATH\\TO\\MEDIA\\Playlists" \
  --m3u8-local-root "\\SERVERIP\\PATH\\TO\\MEDIA\\Playlists" \
  --m3u8-plex-root "/media/music/Playlists" \
  --library-name "Musik" --plex-url "http://localhost:32400" --plex-token "your_token" \
  --use-existing --verify-m3u8 --verbose
```

## How It Works (Technical Overview)

### 1. Plex Section Lookup

PlexSyncer queries your Plex server using the provided `--plex-url` and `--plex-token`, retrieves all library sections,
and matches one by the provided `--library-name`. The resulting section key is used for uploading playlists.

### 2. Playlist Generation

PlexSyncer scans each subdirectory within the `--playlist-folder`. For each directory:

- If regenerating (or not using existing playlists), it creates a new m3u8 file named after the subdirectory.
- It scans for media files (by extension), extracts metadata (duration, title) using `mutagen` if available, and writes
  entries in the format:

  ```plaintext
  #EXTINF:duration,title
  remapped_path
  ```

- File paths are remapped by replacing the `--m3u8-local-root` prefix with the `--m3u8-plex-root` value.

### 3. Uploading

If not in generate-only mode, each generated (or existing) playlist is uploaded to the Plex API endpoint. Before
uploading, if the file ends in `.m3u8`, it is renamed to `.m3u`.

### 4. Verification

- **Playlist Existence Verification**: Using `--verify-uploads`, PlexSyncer confirms that each local playlist (
  identified by its folder name) exists on Plex.
- **Content Verification**: With `--verify-m3u8`, PlexSyncer reads each local m3u8 file, extracts track titles, and
  compares these against the corresponding Plex playlist items.

### 5. Master Playlist Generation

PlexSyncer creates a master m3u8 file (`master.m3u8`), listing all remapped playlist paths, offering a single entry
point to access all individual playlists.
