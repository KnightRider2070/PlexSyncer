import json
import os
import shutil
import unittest
from unittest.mock import MagicMock, patch

from integrations.spotify import (
    create_playlist_json_structure,
    extract_playlist_id,
    sanitize_filename,
)


class TestSpotifyIntegration(unittest.TestCase):
    def setUp(self):
        # Create temporary directories for tests.
        self.test_dir = os.path.join("tests", "tmp")
        self.output_dir = os.path.join("tests", "tmp_output")
        os.makedirs(self.test_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

    def tearDown(self):
        # Remove temporary directories after tests.
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir)

    def test_extract_playlist_id_url(self):
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abcdef"
        expected = "37i9dQZF1DXcBWIGoYBM5M"
        result = extract_playlist_id(url)
        self.assertEqual(result, expected)

    def test_extract_playlist_id_uri(self):
        uri = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        expected = "37i9dQZF1DXcBWIGoYBM5M"
        result = extract_playlist_id(uri)
        self.assertEqual(result, expected)

    def test_sanitize_filename(self):
        original = "My:Playlist/Name?"
        sanitized = sanitize_filename(original)
        self.assertNotIn(":", sanitized)
        self.assertNotIn("/", sanitized)
        self.assertNotIn("?", sanitized)

    def test_create_playlist_json_structure(self):
        # Test input data (simulate a downloaded JSON file)
        test_data = {
            "playlists": [
                {
                    "name": "Test Playlist",
                    "lastModifiedDate": "2025-03-06",
                    "items": [
                        {
                            "track": {
                                "trackName": "Test Song",
                                "artistName": "Test Artist",
                                "albumName": "Test Album",
                                "trackUri": "spotify:track:12345",
                            },
                            "episode": None,
                            "audiobook": None,
                            "localTrack": None,
                            "addedDate": "2024-12-18",
                        }
                    ],
                }
            ]
        }
        # Write test input JSON file.
        input_json_path = os.path.join(self.test_dir, "playlist_data.json")
        with open(input_json_path, "w", encoding="utf-8") as f:
            json.dump(test_data, f)

        # Define output file path for generated JSON structure.
        output_json_path = os.path.join(self.output_dir, "output_playlist.json")

        # Run the function to create the JSON structure.
        create_playlist_json_structure(
            input_json_file=input_json_path,
            output_json_file=output_json_path,
            encode_spaces=False,
        )

        # Verify that the output file exists and has the expected structure.
        self.assertTrue(os.path.exists(output_json_path))
        with open(output_json_path, "r", encoding="utf-8") as f:
            output_data = json.load(f)
        self.assertIn("playlists", output_data)
        self.assertEqual(len(output_data["playlists"]), 1)
        playlist = output_data["playlists"][0]
        self.assertEqual(playlist.get("name"), "Test Playlist")
        self.assertIn("tracks", playlist)
        self.assertEqual(len(playlist["tracks"]), 1)
        track = playlist["tracks"][0]
        self.assertEqual(track.get("trackName"), "Test Song")
        self.assertEqual(track.get("artistName"), "Test Artist")
        self.assertEqual(track.get("albumName"), "Test Album")
        self.assertEqual(track.get("trackUri"), "spotify:track:12345")

    @patch("integrations.spotify.get_spotify_client")
    def test_create_playlist_json_from_spotify_url(self, mock_get_client):
        """
        Test creating a JSON structure from a Spotify playlist URL using mocked Spotipy responses.
        """
        # Create a dummy Spotify client with expected behavior.
        dummy_client = MagicMock()
        dummy_client.playlist.return_value = {"name": "Dummy Playlist"}
        dummy_items = {
            "items": [
                {
                    "track": {
                        "name": "Dummy Song",
                        "artists": [{"name": "Dummy Artist"}],
                        "album": {"name": "Dummy Album"},
                        "uri": "spotify:track:dummy123",
                    }
                }
            ],
            "next": None,
        }
        dummy_client.playlist_items.return_value = dummy_items
        mock_get_client.return_value = dummy_client

        test_playlist_url = "https://open.spotify.com/playlist/dummyplaylist?si=1234"
        output_json_path = os.path.join(self.output_dir, "spotify_output.json")
        client_id = "dummy_client_id"
        client_secret = "dummy_client_secret"

        from integrations.spotify import create_playlist_json_from_spotify_url

        create_playlist_json_from_spotify_url(
            playlist_url=test_playlist_url,
            client_id=client_id,
            client_secret=client_secret,
            output_json_file=output_json_path,
            encode_spaces=False,
        )

        self.assertTrue(os.path.exists(output_json_path))
        with open(output_json_path, "r", encoding="utf-8") as f:
            output_data = json.load(f)
        self.assertIn("playlist", output_data)
        playlist = output_data["playlist"]
        self.assertEqual(playlist.get("name"), "Dummy Playlist")
        self.assertIn("tracks", playlist)
        self.assertEqual(len(playlist["tracks"]), 1)
        track = playlist["tracks"][0]
        self.assertEqual(track.get("trackName"), "Dummy Song")
        self.assertEqual(track.get("artistName"), "Dummy Artist")
        self.assertEqual(track.get("albumName"), "Dummy Album")
        self.assertEqual(track.get("trackUri"), "spotify:track:dummy123")
        os.remove(output_json_path)

    @unittest.skipUnless(
        os.environ.get("SPOTIFY_CLIENT_ID")
        and os.environ.get("SPOTIFY_CLIENT_SECRET")
        and os.environ.get("SPOTIFY_PLAYLIST_URL"),
        "Real Spotify credentials not provided in environment variables.",
    )
    def test_real_playlist(self):
        """
        Test creating a JSON structure from a real Spotify playlist URL.
        Environment variables SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, and SPOTIFY_PLAYLIST_URL must be set.
        """
        client_id = os.environ.get("SPOTIFY_CLIENT_ID")
        client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
        playlist_url = os.environ.get("SPOTIFY_PLAYLIST_URL")
        output_json_path = os.path.join(self.output_dir, "real_spotify_output.json")

        from integrations.spotify import create_playlist_json_from_spotify_url

        create_playlist_json_from_spotify_url(
            playlist_url=playlist_url,
            client_id=client_id,
            client_secret=client_secret,
            output_json_file=output_json_path,
            encode_spaces=False,
        )

        # Check that the output JSON file exists and has at least one track.
        self.assertTrue(os.path.exists(output_json_path))
        with open(output_json_path, "r", encoding="utf-8") as f:
            output_data = json.load(f)
        self.assertIn("playlist", output_data)
        playlist = output_data["playlist"]
        self.assertIn("tracks", playlist)
        self.assertGreater(len(playlist["tracks"]), 0)
        # Optionally, print out the first track for manual inspection.
        print("First track from real playlist:", playlist["tracks"])
        print("First track from real playlist:", playlist["tracks"][0])


if __name__ == "__main__":
    unittest.main()
