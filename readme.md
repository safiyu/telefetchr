
# Telefetchr (Telegram file downloader)

## Prerequisites

- Docker installed on your system
- Docker Compose installed (usually comes with Docker Desktop)
- Telegram API credentials (api_id and api_hash from https://my.telegram.org)

## Directory Structure



```
telefetchr/
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── requirements.txt
├── launch.py                # FastAPI app entry point
├── config.yaml
├── view.html                # Modern web UI (Tailwind, FontAwesome, popup toasts)
├── script.js   # JavaScript for the web UI
├── downloads/               # Created automatically
└── sessions/                # Created automatically, stores session files
  ├── session.session
  ├── telegram_session.session
  └── telegram_session.session-journal
```


> **Note:** All static files (HTML, JS) are served from the project root at `/static` by FastAPI. You do not need a physical `static/` folder.

## Configuration File: `config.yaml`

The `config.yaml` file in the project root contains your Telegram API credentials and app settings. **This file is required for the app to run.** Use the config template to create the config.yaml.

**Template:**

```yaml
api_id: YOUR_API_ID
api_hash: YOUR_API_HASH
phone: YOUR_PHONE_NUMBER  # Without + sign, e.g., 33612345678
save_path: downloads # local path or network drive url
channel: ["channel1", "channel2"]
max_concurrent_downloads: 3
```

**Security:**
- `config.yaml` is listed in `.gitignore` and will not be committed to git.
- Never share this file or commit it to public repositories.

## Building and Running

### Option 1: Using Docker Compose (Recommended)

```bash
# Build and start the container
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the container
docker-compose down
```

### Option 2: Using Docker directly

```bash
# Build the image
docker build -t telefetchr .

# Run the container
docker run -d \
  --name telefetchr \
  -p 8000:8000 \
  -v $(pwd)/downloads:/app/downloads \
  -v $(pwd)/sessions:/app/sessions \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  telefetchr

# View logs
docker logs -f telefetchr

# Stop the container
docker stop telefetchr
docker rm telefetchr
```


## Accessing the Application

Once the container is running, open your browser and navigate to:

```
http://localhost:8000
```

## Running Without Docker (Development)

You can run the FastAPI app directly for development:


```bash
pip install -r requirements.txt
python launch.py
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

## First Time Login

1. The application will prompt you to login
2. Click "Send Verification Code"
3. Enter the code you receive via Telegram
4. If you have 2FA enabled, enter your password
5. The session will be saved in the `sessions/` directory

## Persistent Data

- **Downloads**: All downloaded files are stored in save path in config
- **Sessions**: Telegram session files are stored in `./sessions/`
- Both directories are mounted as volumes, so data persists even if you restart or rebuild the container

## Network Paths for Downloads

If you want to download files to a network location:

### Linux/Mac:
```bash
# Mount network share first
sudo mount -t cifs //server/share /mnt/network -o username=user,password=pass

# Update docker-compose.yml to add volume:
volumes:
  - /mnt/network:/mnt/network
```

### Windows:
```powershell
# Map network drive first (e.g., Z:)
net use Z: \\server\share /user:username password

# Update docker-compose.yml:
volumes:
  - Z:/:/network
```

Then in the web interface, use `/mnt/network` (Linux/Mac) or `/network` (Windows) as the download path.

## Useful Commands

```bash
# Rebuild after code changes
docker-compose up -d --build

# View real-time logs
docker-compose logs -f

# Restart the service
docker-compose restart

# Stop and remove everything
docker-compose down

# Stop and remove including volumes (WARNING: Deletes session data)
docker-compose down -v

# Access container shell
docker-compose exec telefetchr /bin/bash

# Check container status
docker-compose ps
```

## Troubleshooting

### Container won't start
```bash
# Check logs
docker-compose logs

# Check if port 8000 is already in use
netstat -an | grep 8000  # Linux/Mac
netstat -an | findstr 8000  # Windows
```

### Session issues
```bash
# Remove old session files
rm -rf sessions/*

# Restart container
docker-compose restart
```

### Permission issues with volumes
```bash
# Linux: Fix permissions
sudo chown -R $USER:$USER downloads sessions

# Or run container with your user ID
# Add to docker-compose.yml under service:
user: "${UID}:${GID}"
```

### Network path not accessible
- Ensure the network share is mounted on the host
- Check that the Docker container has access to the mounted path
- For Windows, use Docker Desktop's Settings > Resources > File Sharing

## Security Notes

- **Never commit `config.yaml`** with real credentials to version control
- Add `config.yaml` to `.gitignore`
- Consider using environment variables for sensitive data:

```yaml
# docker-compose.yml
environment:
  - API_ID=${API_ID}
  - API_HASH=${API_HASH}
  - PHONE=${PHONE}
```

Then modify `downloader.py` to read from environment variables:

```python
import os

API_ID = os.getenv('API_ID') or config['api_id']
API_HASH = os.getenv('API_HASH') or config['api_hash']
PHONE_NUMBER = os.getenv('PHONE') or f"+{config['phone']}"
```

## Updating the Application

```bash
# Pull latest changes
git pull

# Rebuild and restart
docker-compose up -d --build
```

## Production Deployment

For production use:

1. Use a reverse proxy (nginx/Caddy) with SSL
2. Set up proper authentication
3. Use environment variables for secrets
4. Enable Docker health checks
5. Set resource limits in docker-compose.yml:

```yaml
deploy:
  resources:
    limits:
      cpus: '1'
      memory: 512M
    reservations:
      cpus: '0.5'
      memory: 256M
```

## License

This project is free to clone and modify.
