from flask import Flask, jsonify
from redis import Redis
import os

app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify(ok=True)

@app.get("/health/redis")
def health_redis():
    r = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), socket_connect_timeout=3)
    try:
        r.ping()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500