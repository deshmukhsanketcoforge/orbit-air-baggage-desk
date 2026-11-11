# STRETCH GOAL 4
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Run the app:   docker run --rm orbit-air <command>
# Run the tests: docker run --rm --entrypoint pytest orbit-air
ENTRYPOINT ["python", "main.py"]
