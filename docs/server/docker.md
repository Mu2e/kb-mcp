# Docker Deployment

Container runs HTTP (no HTTPS) - meant for reverse proxy or Cloud Run deployment. For HTTPS in Docker, see comments in Dockerfile.

## Build

```bash
docker build -t test-mcp .
```

## Run

```bash
docker run -d -p 8443:8443 \
  -e GITHUB_CLIENT_ID=your_client_id \
  -e GITHUB_CLIENT_SECRET=your_client_secret \
  -e GITHUB_REQUIRED_REPO=owner/repo \
  --name test-mcp \
  test-mcp
```

**Note**: GitHub OAuth environment variables only needed if using OAuth authentication. For API key only deployments, these can be omitted.

## Test

```bash
curl http://localhost:8443/status
```

## Stop

```bash
docker stop test-mcp
docker rm test-mcp
```
