# Installation

## 1. Python Dependencies

```bash
pip install -e .
```

## 2. ngrok Setup

ngrok is required to expose your local server to Claude Desktop.

```bash
# Install ngrok
brew install ngrok

# Sign up at https://ngrok.com and get your authtoken
# Then authenticate
ngrok authtoken YOUR_NGROK_TOKEN
```

## 3. GitHub OAuth App Setup

1. Go to https://github.com/settings/developers
2. Click "New OAuth App"
3. Fill in:
   - **Application name**: test-mcp (or any name)
   - **Homepage URL**: `https://your-ngrok-url` (you'll update this after starting ngrok)
   - **Authorization callback URL**: `https://your-ngrok-url/oauth/github/callback`
4. Click "Register application"
5. Copy the **Client ID** and generate a **Client secret**

## 4. Environment Configuration

```bash
cp .env.example .env
```

Edit `.env` and set:
- `GITHUB_CLIENT_ID` - Your GitHub OAuth App Client ID
- `GITHUB_CLIENT_SECRET` - Your GitHub OAuth App Client Secret
- `GITHUB_REQUIRED_REPO` - Repository to restrict access (format: `owner/repo`)
- `BASE_URL` - Your ngrok URL (update after starting ngrok)

## 5. SSL Certificates

Install and use mkcert to create locally-trusted certificates:

```bash
# Install mkcert
brew install mkcert

# Install local CA
mkcert -install

# Generate certificates
mkdir -p certs
mkcert -key-file certs/key.pem -cert-file certs/cert.pem localhost 127.0.0.1
```

## 6. Start the Server

```bash
./scripts/start.sh
```

The script will:
1. Start ngrok tunnel and display the public URL
2. Start the MCP server

Update your `.env` file's `BASE_URL` with the ngrok URL shown, and update your GitHub OAuth App's callback URL to match.

## 7. Connect Claude Desktop

1. Open Claude Desktop
2. Go to Connectors
3. Click "Add custom connector"
4. Enter:
   - **Name**: test-mcp
   - **URL**: `https://your-ngrok-url/mcp`
5. Click "Connect" (and pray)
6. Browser will open for GitHub OAuth flow
