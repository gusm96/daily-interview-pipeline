FROM python:3.11-slim

WORKDIR /app

# Copy dependencies first to leverage Docker caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the entrypoint source code
COPY main.py .

# Functions Framework uses port 8080 by default on GCP
EXPOSE 8080

# Run using functions-framework CLI
CMD ["functions-framework", "--target=daily_interview_bot"]
