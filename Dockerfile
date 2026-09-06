FROM node:20-bookworm-slim

# Chrome + Xvfb (roda o Chrome "headful" sob display virtual, evitando
# o bloqueio anti-bot que o modo --headless costuma sofrer no e-SAJ/EPROC)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg curl unzip xvfb fonts-liberation \
    python3 python3-pip python3-venv \
    && wget -q -O /usr/share/keyrings/google-chrome.gpg https://dl.google.com/linux/linux_signing_key.pub \
    && echo "deb [signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/google-chrome
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHON_BIN=/opt/venv/bin/python3

COPY . .

EXPOSE 8000
CMD ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24", "node", "backend-datajud.js"]
