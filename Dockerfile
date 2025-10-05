FROM python:3.11-slim

WORKDIR /app

# Copy application files
COPY bambucuts/ /app/bambucuts/
COPY requirements.txt /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose web server port
EXPOSE 5425

# Run the server
CMD ["python", "-m", "bambucuts.webui.app"]
