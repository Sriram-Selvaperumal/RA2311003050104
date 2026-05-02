import os
import sys
import heapq
import requests
from datetime import datetime
from flask import Flask, jsonify, request
from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'logging_middleware'))
from logging_middleware import Log

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)

BASE_URL = "http://20.207.122.201"
PORT = 5002

auth_token = None
notifications_cache = []

PRIORITY_WEIGHT = {
    "Placement": 3,
    "Result": 2,
    "Event": 1
}


def authenticate():
    global auth_token
    if auth_token:
        return auth_token
    resp = requests.post(
        f"{BASE_URL}/evaluation-service/auth",
        json={
            "email": os.getenv("EMAIL"),
            "name": os.getenv("NAME"),
            "rollNo": os.getenv("ROLL_NO"),
            "accessCode": os.getenv("ACCESS_CODE"),
            "clientID": os.getenv("CLIENT_ID"),
            "clientSecret": os.getenv("CLIENT_SECRET"),
        },
        timeout=15
    )
    resp.raise_for_status()
    auth_token = resp.json()["access_token"]
    Log("backend", "info", "auth", "Token acquired for notification service")
    return auth_token


def ensure_auth():
    if not auth_token:
        authenticate()
    return auth_token


def fetch_notifications():
    global notifications_cache
    token = ensure_auth()
    resp = requests.get(
        f"{BASE_URL}/evaluation-service/notifications",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15
    )
    resp.raise_for_status()
    notifications_cache = resp.json()["notifications"]
    Log("backend", "info", "service", f"Notifications loaded: {len(notifications_cache)}")
    return notifications_cache


def score_notification(n):
    type_weight = PRIORITY_WEIGHT.get(n["Type"], 0)
    try:
        ts = datetime.strptime(n["Timestamp"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        ts = datetime.min
    recency_score = ts.timestamp()
    return (type_weight, recency_score)


def get_top_n_notifications(all_notifications, n):
    scored = [(-score_notification(notif)[0], -score_notification(notif)[1], notif) for notif in all_notifications]
    heapq.heapify(scored)
    result = []
    for _ in range(min(n, len(scored))):
        _, _, notif = heapq.heappop(scored)
        result.append(notif)
    return result


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "Campus Notifications Microservice",
        "status": "running",
        "endpoints": [
            "GET /notifications",
            "GET /notifications/priority?n=10"
        ]
    })


@app.route("/notifications", methods=["GET"])
def get_notifications():
    try:
        Log("backend", "info", "handler", "GET /notifications")
        if not notifications_cache:
            fetch_notifications()
        return jsonify({
            "notifications": notifications_cache,
            "total": len(notifications_cache)
        })
    except Exception as err:
        Log("backend", "error", "handler", f"Failed to fetch notifications: {str(err)}")
        return jsonify({"error": str(err)}), 500


@app.route("/notifications/priority", methods=["GET"])
def get_priority_notifications():
    try:
        n = int(request.args.get("n", 10))
        if n <= 0:
            return jsonify({"error": "n must be a positive integer"}), 400

        Log("backend", "info", "handler", f"GET /notifications/priority?n={n}")

        fetch_notifications()

        top_n = get_top_n_notifications(notifications_cache, n)

        result = []
        for notif in top_n:
            type_weight = PRIORITY_WEIGHT.get(notif["Type"], 0)
            result.append({
                "id": notif["ID"],
                "type": notif["Type"],
                "message": notif["Message"],
                "timestamp": notif["Timestamp"],
                "priorityScore": type_weight
            })

        Log("backend", "info", "service", f"Returning top {len(result)} priority notifications")

        return jsonify({
            "top_n": n,
            "returned": len(result),
            "priority_order": "Placement > Result > Event, then by recency",
            "notifications": result
        })
    except Exception as err:
        Log("backend", "error", "handler", f"Priority inbox failed: {str(err)}")
        return jsonify({"error": str(err)}), 500


@app.before_request
def log_incoming():
    Log("backend", "info", "middleware", f"Incoming {request.method} {request.path} from {request.remote_addr}")


@app.after_request
def log_outgoing(response):
    Log("backend", "info", "middleware", f"Response {response.status_code} for {request.method} {request.path}")
    return response


@app.errorhandler(404)
def not_found(e):
    Log("backend", "warn", "route", f"404 Not Found: {request.path}")
    return jsonify({"error": "Route not found"}), 404


@app.errorhandler(500)
def server_error(e):
    Log("backend", "fatal", "handler", f"500 Internal Server Error on {request.path}: {str(e)}")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    print(f"Notification service running at http://localhost:{PORT}")
    print(f"  http://localhost:{PORT}/notifications")
    print(f"  http://localhost:{PORT}/notifications/priority?n=10")

    try:
        authenticate()
        fetch_notifications()
        print("Ready.")
    except Exception as err:
        print(f"Init error: {err}")

    app.run(debug=True, host="0.0.0.0", port=PORT)
