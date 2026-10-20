#!/usr/bin/env python3
"""
One-time Google Drive OAuth2 authorization setup.
This script guides you through the OAuth2 flow to get a refresh token.

Usage:
  python scripts/setup_google_drive_oauth.py --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET
"""

import argparse
import json
import webbrowser
import io
from urllib.parse import urlencode, parse_qs
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
import sys

# Force UTF-8 output on Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth2 redirect callback."""

    auth_code = None

    def do_GET(self):
        """Handle GET request with authorization code."""
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = parse_qs(query)

        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html = """
            <html>
            <head><title>Authorization Successful</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1>Authorization Successful!</h1>
                <p>You have successfully authorized the application.</p>
                <p>You can close this window and return to the terminal.</p>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Authorization Failed</h1></body></html>")

    def log_message(self, format, *args):
        """Suppress HTTP server logging."""
        pass


def get_refresh_token(client_id, client_secret):
    """Get refresh token through OAuth2 flow."""

    print("=" * 60)
    print("Google Drive OAuth2 Setup")
    print("=" * 60)
    print()
    print("This script will authorize your personal Google Drive account.")
    print()

    # Start callback server
    print("Starting local callback server on http://localhost:8080...")
    server = HTTPServer(("localhost", 8080), OAuthCallbackHandler)

    # Build authorization URL
    auth_params = {
        "client_id": client_id,
        "redirect_uri": "http://localhost:8080/callback",
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive",
        "access_type": "offline",
        "prompt": "consent",  # Force consent to get refresh token
    }

    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(auth_params)}"

    print()
    print("Opening Google login page in your browser...")
    print(f"If it doesn't open, visit: {auth_url}")
    print()

    webbrowser.open(auth_url)

    print("Waiting for authorization response...")

    # Handle one request (the callback)
    server.handle_request()
    server.server_close()

    if not OAuthCallbackHandler.auth_code:
        print("❌ Authorization failed - no code received")
        sys.exit(1)

    auth_code = OAuthCallbackHandler.auth_code
    print(f"✅ Authorization code received: {auth_code[:20]}...")
    print()

    # Exchange code for tokens
    print("Exchanging authorization code for tokens...")

    token_params = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": "http://localhost:8080/callback",
    }

    try:
        body = urlencode(token_params).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token", data=body
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            token_response = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"❌ Token exchange failed: {error_body}")
        sys.exit(1)

    refresh_token = token_response.get("refresh_token")
    if not refresh_token:
        print("❌ No refresh token in response - ensure 'access_type=offline' and 'prompt=consent'")
        sys.exit(1)

    print("✅ Tokens obtained successfully!")
    print()
    print("=" * 60)
    print("🔐 REFRESH TOKEN (save this securely):")
    print("=" * 60)
    print(refresh_token)
    print("=" * 60)
    print()
    print("📋 Next steps:")
    print("1. Copy the refresh token above")
    print("2. Add it to GitHub secrets as: GOOGLE_DRIVE_REFRESH_TOKEN")
    print("3. Also add:")
    print("   - GOOGLE_OAUTH_CLIENT_ID: " + client_id)
    print("   - GOOGLE_OAUTH_CLIENT_SECRET: " + client_secret)
    print()
    print("Command to set GitHub secrets:")
    print(f"  gh secret set GOOGLE_DRIVE_REFRESH_TOKEN --body '{refresh_token}'")
    print(f"  gh secret set GOOGLE_OAUTH_CLIENT_ID --body '{client_id}'")
    print(f"  gh secret set GOOGLE_OAUTH_CLIENT_SECRET --body '{client_secret}'")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Set up Google Drive OAuth2 for personal account"
    )
    parser.add_argument("--client-id", required=True, help="Google OAuth2 Client ID")
    parser.add_argument("--client-secret", required=True, help="Google OAuth2 Client Secret")

    args = parser.parse_args()

    get_refresh_token(args.client_id, args.client_secret)


if __name__ == "__main__":
    main()
