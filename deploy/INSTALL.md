# AI Trading Team — Installation Guide (Ubuntu/Linux)

## Prerequisites

- Ubuntu 20.04+ or Debian 11+ (any systemd-based Linux works)
- Python 3.9+
- Git
- Internet access (for Alpaca API)
- Alpaca paper trading account ([sign up free](https://app.alpaca.markets))

---

## Quick Install (Automated)

```bash
# 1. Clone the repo
git clone https://github.com/mihaibalaci/trading-team.git
cd trading-team

# 2. Run the installer (requires root)
sudo bash deploy/install.sh

# 3. Configure your Alpaca API keys
sudo cp /opt/trading-team/signals/.env.example /opt/trading-team/signals/.env
sudo nano /opt/trading-team/signals/.env
# Fill in ALPACA_API_KEY and ALPACA_SECRET_KEY

# 4. Set file ownership
sudo chown trader:trader /opt/trading-team/signals/.env

# 5. Start the service
sudo systemctl start trading-team

# 6. Open the dashboard
# http://your-server-ip:5050
# Default login: admin / admin123
```

---

## Manual Install (Step by Step)

### Step 1 — System packages

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git rsync
```

### Step 2 — Create a service user

```bash
sudo useradd --system --create-home --shell /bin/bash trader
```

### Step 3 — Clone the application

```bash
sudo mkdir -p /opt/trading-team
sudo git clone https://github.com/mihaibalaci/trading-team.git /opt/trading-team
sudo chown -R trader:trader /opt/trading-team
```

### Step 4 — Install Python dependencies

```bash
sudo pip3 install -r /opt/trading-team/requirements.txt
```

Or use a virtual environment (recommended for production):

```bash
sudo -u trader python3 -m venv /opt/trading-team/venv
sudo -u trader /opt/trading-team/venv/bin/pip install -r /opt/trading-team/requirements.txt
```

If using a venv, update the `ExecStart` line in the service file:
```
ExecStart=/opt/trading-team/venv/bin/python3 /opt/trading-team/signals/service.py --daemon
```

### Step 5 — Configure Alpaca API keys

```bash
sudo cp /opt/trading-team/signals/.env.example /opt/trading-team/signals/.env
sudo nano /opt/trading-team/signals/.env
```

Fill in your credentials:
```
TRADING_MODE=paper
ALPACA_API_KEY=PKxxxxxxxxxxxxxxxxxx
ALPACA_SECRET_KEY=your_secret_key_here
```

Set permissions (only the service user should read this):
```bash
sudo chown trader:trader /opt/trading-team/signals/.env
sudo chmod 600 /opt/trading-team/signals/.env
```

### Step 6 — Generate a Flask secret key

```bash
FLASK_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
sudo sed -i "s/change-this-to-a-random-secret/$FLASK_SECRET/" \
    /opt/trading-team/deploy/trading-team.service
```

### Step 7 — Install the systemd service

```bash
sudo cp /opt/trading-team/deploy/trading-team.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trading-team
```

### Step 8 — Start the service

```bash
sudo systemctl start trading-team
```

### Step 9 — Verify

```bash
# Check service status
sudo systemctl status trading-team

# Watch the boot sequence in real time
sudo journalctl -u trading-team -f
```

You should see:
```
[Kai       ] INFO  Starting — testing broker connection...
[Kai       ] INFO  Connected — paper mode, equity $100,000.00
[Clio      ] INFO  Starting — loading strategies into memory...
[Clio      ] INFO  All 6 strategies loaded. Clio standing by.
[Mira      ] INFO  Starting — risk monitoring active.
[Finn      ] INFO  Dependencies ready. Loading strategies from Clio...
[Finn      ] INFO  Loaded 6 strategies. Starting scan loop.
[Remy      ] INFO  Kai ready. Listening for signals from Finn.
[Larry     ] INFO  Dashboard ready at http://localhost:5050
```

### Step 10 — Access the dashboard

Open `http://your-server-ip:5050` in a browser.

Default credentials: `admin` / `admin123`

Change the admin password after first login.

---

## Service Management

| Command | Description |
|---------|-------------|
| `sudo systemctl start trading-team` | Start the service |
| `sudo systemctl stop trading-team` | Graceful shutdown (closes all positions) |
| `sudo systemctl restart trading-team` | Restart all agents |
| `sudo systemctl status trading-team` | Check if running |
| `sudo systemctl enable trading-team` | Auto-start on boot |
| `sudo systemctl disable trading-team` | Disable auto-start |
| `sudo journalctl -u trading-team -f` | Live log stream |
| `sudo journalctl -u trading-team --since "1 hour ago"` | Recent logs |
| `sudo journalctl -u trading-team -p err` | Errors only |

---

## Agent Boot Sequence

The service starts agents in this order. Each agent waits for its dependencies:

```
1. Kai   (broker)     — connects to Alpaca, tests health
                         ↓ must pass before anything else
2. Clio  (strategies) — loads all 6 strategy profiles into memory
                         ↓ strategies available for Finn
3. Mira  (risk)       — starts monitoring equity and drawdown
4. Finn  (scanner)    — waits for Kai + Clio, then scans all strategies
                         ↓ sends valid signals to Remy
5. Remy  (execution)  — waits for Kai, then listens for signals
6. Larry (dashboard)  — starts Flask web UI on port 5050
```

If Kai fails to connect within 30 seconds, the entire service aborts.

---

## Firewall Configuration

If you need external access to the dashboard:

```bash
# UFW (Ubuntu default)
sudo ufw allow 5050/tcp

# Or iptables
sudo iptables -A INPUT -p tcp --dport 5050 -j ACCEPT
```

For production, put nginx in front as a reverse proxy with HTTPS:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

Example nginx config (`/etc/nginx/sites-available/trading-team`):
```nginx
server {
    listen 80;
    server_name trading.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then:
```bash
sudo ln -s /etc/nginx/sites-available/trading-team /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d trading.yourdomain.com
```

---

## Updating

```bash
cd /opt/trading-team
sudo -u trader git pull
sudo systemctl restart trading-team
```

---

## Database

SQLite database is stored at `/opt/trading-team/signals/trading_team.db`.

To back up:
```bash
sudo -u trader sqlite3 /opt/trading-team/signals/trading_team.db ".backup /tmp/trading_team_backup.db"
```

Tables: `users`, `trades`, `daily_stats`, `scanner_sessions`, `strategy_configs`, `signals_log`.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Service won't start | Check `sudo journalctl -u trading-team -n 50` for errors |
| Kai fails to connect | Verify `.env` has correct Alpaca keys. Test: `python3 signals/kai_connect_test.py` |
| Dashboard not loading | Check if port 5050 is in use: `sudo ss -tlnp \| grep 5050` |
| Permission denied | Run `sudo chown -R trader:trader /opt/trading-team` |
| Python module not found | Run `sudo pip3 install -r /opt/trading-team/requirements.txt` |
| No trades executing | Check if market is open. Finn only scans during session hours. |
| Mira halted trading | Drawdown exceeded limit. Check `journalctl` for Mira's messages. |

---

## Uninstall

```bash
sudo systemctl stop trading-team
sudo systemctl disable trading-team
sudo rm /etc/systemd/system/trading-team.service
sudo systemctl daemon-reload
sudo rm -rf /opt/trading-team
sudo userdel -r trader
```
